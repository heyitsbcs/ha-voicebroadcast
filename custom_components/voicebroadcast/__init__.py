"""The Voice Broadcast integration.

Lets you say "broadcast <message>" to Assist and hear your own voice on
the other media players, instead of a TTS robot re-reading your words.

The integration ships:

- a transparent STT router (the pipeline's STT engine). It forwards every
  utterance to the upstream STT engine and passes the transcript through
  unchanged. When the first word of the transcript is the trigger word, it
  also saves the utterance audio (minus the trigger word) as a WAV file,
  fires ``voicebroadcast.recorded`` and, optionally, broadcasts it.
- a conversation agent that answers trigger-word utterances with a short
  confirmation (default "Sent.") instead of a long robot reply, and
  delegates everything else to the default agent.
- a media source exposing the recordings, plus ``voicebroadcast.broadcast``
  and ``voicebroadcast.clear`` services.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_entity_ids
from homeassistant.helpers.typing import ConfigType

from . import broadcast as broadcast_helper
from . import recorder
from .const import (
    CONF_ALIASES,
    CONF_AGENT,
    CONF_AUTO_BROADCAST,
    CONF_CONFIRM,
    CONF_DUCK_VOLUME,
    CONF_KEEP_HOURS,
    CONF_PREANNOUNCE,
    CONF_SATELLITES,
    CONF_KEYWORD,
    CONF_LANGUAGES,
    CONF_MAX_DURATION,
    CONF_OUTPUT_DIR,
    CONF_TARGETS,
    CONF_UPSTREAM,
    DEFAULT_AUTO_BROADCAST,
    DEFAULT_CONFIRM,
    DEFAULT_DUCK_VOLUME,
    DEFAULT_KEEP_HOURS,
    DEFAULT_PREANNOUNCE,
    DEFAULT_KEYWORD,
    DEFAULT_LANGUAGES,
    DEFAULT_MAX_DURATION,
    DEFAULT_OUTPUT_DIR,
    DOMAIN,
    MEDIA_VIEW_URL,
    SATELLITE_DOMAIN,
    SERVICE_BROADCAST,
    SERVICE_CLEAR,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {vol.Optional(DOMAIN): vol.Schema({})}, extra=vol.ALLOW_EXTRA
)

SERVICE_BROADCAST_SCHEMA = vol.Schema(
    {
        vol.Optional("media_source_id"): cv.string,
        vol.Optional("url"): cv.string,
        vol.Optional("volume"): vol.Coerce(float),
    }
)

SERVICE_CLEAR_SCHEMA = vol.Schema({})


class BroadcastMediaView(HomeAssistantView):
    """Serve recorded WAV files to media players (unguessable ULID names)."""

    url = MEDIA_VIEW_URL
    name = "api:voicebroadcast"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, filename: str):
        """Return a WAV file."""
        if not filename.endswith(".wav") or "/" in filename:
            return web.Response(status=404)
        for cfg in self.hass.data.get(DOMAIN, {}).values():
            path = Path(cfg[CONF_OUTPUT_DIR]) / filename
            if path.is_file():
                return web.Response(body=path.read_bytes(), content_type="audio/wav")
        return web.Response(status=404)


def entry_config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Merge entry data (and options) with defaults and derive paths."""
    data = dict(entry.data)
    data.update(entry.options or {})
    config: dict[str, Any] = {
        CONF_UPSTREAM: data[CONF_UPSTREAM],
        CONF_KEYWORD: data.get(CONF_KEYWORD, DEFAULT_KEYWORD),
        CONF_ALIASES: data.get(CONF_ALIASES, []),
        CONF_OUTPUT_DIR: str(Path(hass.config.path(data.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)))),
        CONF_AGENT: data.get(CONF_AGENT),
        CONF_AUTO_BROADCAST: data.get(CONF_AUTO_BROADCAST, DEFAULT_AUTO_BROADCAST),
        CONF_TARGETS: data.get(CONF_TARGETS, []),
        CONF_CONFIRM: data.get(CONF_CONFIRM, DEFAULT_CONFIRM),
        CONF_SATELLITES: data.get(CONF_SATELLITES, []),
        CONF_PREANNOUNCE: data.get(CONF_PREANNOUNCE, DEFAULT_PREANNOUNCE),
        CONF_DUCK_VOLUME: data.get(CONF_DUCK_VOLUME, DEFAULT_DUCK_VOLUME),
        CONF_MAX_DURATION: data.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION),
        CONF_KEEP_HOURS: data.get(CONF_KEEP_HOURS, DEFAULT_KEEP_HOURS),
        CONF_LANGUAGES: data.get(CONF_LANGUAGES, DEFAULT_LANGUAGES),
        "keywords": recorder.keyword_set(
            data.get(CONF_KEYWORD, DEFAULT_KEYWORD), data.get(CONF_ALIASES, [])
        ),
        "entry_id": entry.entry_id,
    }
    Path(config[CONF_OUTPUT_DIR]).mkdir(parents=True, exist_ok=True)
    return config


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (config entries only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Voice Broadcast from a config entry."""
    config = entry_config(hass, entry)
    # async_setup is not re-run on reload, so make sure the bucket exists
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = config

    # Reload the entry when its options change so new settings (pass-through
    # assistant, targets, ...) take effect immediately.
    entry.async_on_unload(
        entry.add_update_listener(
            lambda _hass, updated_entry: hass.config_entries.async_reload(
                updated_entry.entry_id
            )
        )
    )

    if not any(cfg.get("view_registered") for cfg in hass.data[DOMAIN].values()):
        hass.http.register_view(BroadcastMediaView(hass))
        config["view_registered"] = True

    async def _async_handle_broadcast(call: ServiceCall) -> None:
        # Explicit targets override the configured lists; both media
        # players and voice satellites may be targeted.
        entity_ids = {
            entity_id
            for entity_id in await async_extract_entity_ids(call)
            if entity_id.startswith("media_player.")
            or entity_id.startswith(SATELLITE_DOMAIN + ".")
        }
        media_source_id = call.data.get("media_source_id")
        url = call.data.get("url")
        if not media_source_id and not url:
            raise HomeAssistantError(
                "voicebroadcast.broadcast requires media_source_id or url"
            )
        await broadcast_helper.async_broadcast(
            hass,
            config,
            media_source_id=media_source_id,
            url=url,
            volume=call.data.get("volume"),
            entity_ids=list(entity_ids) or None,
        )

    async def _async_handle_clear(call: ServiceCall) -> None:
        count = await recorder.async_clear_recordings(
            hass, Path(config[CONF_OUTPUT_DIR])
        )
        _LOGGER.info("Cleared %d broadcast recording(s)", count)

    if not hass.services.has_service(DOMAIN, SERVICE_BROADCAST):
        hass.services.async_register(
            DOMAIN,
            SERVICE_BROADCAST,
            _async_handle_broadcast,
            schema=SERVICE_BROADCAST_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CLEAR, _async_handle_clear, schema=SERVICE_CLEAR_SCHEMA
        )

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.STT, Platform.CONVERSATION]
    )
    _LOGGER.info("Voice Broadcast set up (keyword: %s)", config[CONF_KEYWORD])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if await hass.config_entries.async_unload_platforms(
        entry, [Platform.STT, Platform.CONVERSATION]
    ):
        del hass.data[DOMAIN][entry.entry_id]
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
            hass.services.async_remove(DOMAIN, SERVICE_BROADCAST)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR)
        return True
    return False


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entries."""
    return True
