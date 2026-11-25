"""Broadcast recordings to media players."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er_helper, event

from . import recorder
from .const import (
    CONF_DUCK_VOLUME,
    CONF_OUTPUT_DIR,
    CONF_PREANNOUNCE,
    CONF_SATELLITES,
    CONF_TARGETS,
    DOMAIN,
    SATELLITE_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_RESTORE_FALLBACK_EXTRA_SECONDS = 10
_RESTORE_TIMEOUT_SECONDS = 60 * 60


async def async_broadcast(
    hass: HomeAssistant,
    config: dict[str, Any],
    media_source_id: str | None = None,
    url: str | None = None,
    volume: float | None = None,
    entity_ids: list[str] | None = None,
) -> list[str]:
    """Play a recording on the configured (or explicitly given) targets.

    Players that support MEDIA_ANNOUNCE get a native announce (music is
    paused and resumed by the player itself). Everything else gets a
    volume duck with restore. Voice satellites (assist_satellite) get a
    native announcement with the recording as media_id, optionally
    preceded by their chime.

    `entity_ids` (mixed media_player.* / assist_satellite.*) override
    the configured lists; a device that exists in both domains plays
    only once.
    """
    if media_source_id:
        content_id = media_source_id
    elif url:
        content_id = url
    else:
        return []

    player_ids, satellite_ids = _resolve_targets(hass, config, entity_ids)

    played: list[str] = []
    for player_id in player_ids:
        state = hass.states.get(player_id)
        if state is None or state.state == "unavailable":
            _LOGGER.debug("Skipping %s: unavailable", player_id)
            continue

        supported = state.attributes.get("supported_features", 0) or 0
        supports_announce = bool(supported & MediaPlayerEntityFeature.MEDIA_ANNOUNCE)
        was_playing = state.state == MediaPlayerState.PLAYING
        old_volume = state.attributes.get("volume_level")

        data: dict[str, Any] = {
            "entity_id": player_id,
            "media_content_id": content_id,
            "media_content_type": "music",
        }
        if supports_announce:
            data["announce"] = True
        elif was_playing:
            duck = volume if volume is not None else config[CONF_DUCK_VOLUME]
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": player_id, "volume_level": duck},
                blocking=True,
            )

        await hass.services.async_call(
            "media_player", "play_media", data, blocking=True
        )
        played.append(player_id)

        if was_playing and not supports_announce:
            _schedule_volume_restore(hass, player_id, old_volume, content_id, config)

    played.extend(
        await _announce_satellites(hass, config, content_id, satellite_ids)
    )

    return played


def _resolve_targets(
    hass: HomeAssistant,
    config: dict[str, Any],
    explicit: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Combine the configured media-player and satellite lists.

    The two lists form ONE target set: if anything is listed anywhere,
    only the listed entities receive the broadcast. Only when both are
    empty does "everything" mean all media players and all satellites.

    A device that exists as both a media player and a satellite (e.g.
    ESPHome) plays once: the satellite route wins, the media-player
    route for that device is skipped.
    """
    if explicit:
        player_ids = [
            entity_id for entity_id in explicit
            if entity_id.startswith("media_player.")
        ]
        satellite_ids = [
            entity_id for entity_id in explicit
            if entity_id.startswith(SATELLITE_DOMAIN + ".")
        ]
    else:
        player_ids = list(config.get(CONF_TARGETS) or [])
        satellite_ids = list(config.get(CONF_SATELLITES) or [])
        if not player_ids and not satellite_ids:
            player_ids = hass.states.async_entity_ids("media_player")
            satellite_ids = hass.states.async_entity_ids(SATELLITE_DOMAIN)

    if not player_ids or not satellite_ids:
        return player_ids, satellite_ids

    registry = er_helper.async_get(hass)
    device_players: dict[str, list[str]] = {}
    for player_id in player_ids:
        entity = registry.async_get(player_id)
        if entity and entity.device_id:
            device_players.setdefault(entity.device_id, []).append(player_id)
    if not device_players:
        return player_ids, satellite_ids

    covered_devices = {
        entity.device_id
        for satellite_id in satellite_ids
        if (entity := registry.async_get(satellite_id)) and entity.device_id
        and entity.device_id in device_players
    }
    kept_players: list[str] = []
    for player_id in player_ids:
        entity = registry.async_get(player_id)
        if entity and entity.device_id in covered_devices:
            # same physical device: the satellite route plays it once
            continue
        kept_players.append(player_id)
    return kept_players, satellite_ids


async def _announce_satellites(
    hass: HomeAssistant,
    config: dict[str, Any],
    content_id: str,
    satellite_ids: list[str],
) -> list[str]:
    """Announce the recording on voice satellites via assist_satellite.announce."""
    if not satellite_ids or not hass.services.has_service(SATELLITE_DOMAIN, "announce"):
        return []

    played: list[str] = []
    for satellite_id in satellite_ids:
        state = hass.states.get(satellite_id)
        if state is None or state.state == "unavailable":
            _LOGGER.debug("Skipping satellite %s: unavailable", satellite_id)
            continue
        supported = state.attributes.get("supported_features", 0) or 0
        # AssistSatelliteEntityFeature.ANNOUNCE == 1
        if not supported & 1:
            _LOGGER.debug("Skipping satellite %s: no announce support", satellite_id)
            continue
        await hass.services.async_call(
            SATELLITE_DOMAIN,
            "announce",
            {
                "entity_id": satellite_id,
                "media_id": content_id,
                "preannounce": config.get(CONF_PREANNOUNCE, True),
            },
            blocking=True,
        )
        played.append(satellite_id)
    return played


def _schedule_volume_restore(
    hass: HomeAssistant,
    player_id: str,
    old_volume: float | None,
    content_id: str,
    config: dict[str, Any],
) -> None:
    """Restore volume (and playback) when the announcement finishes.

    A state listener fires the restore as soon as the player stops; a
    duration-based timer is the fallback.
    """
    duration: float | None = None
    if content_id.startswith(f"media-source://{DOMAIN}/"):
        filename = content_id.rsplit("/", 1)[-1]
        path = Path(config[CONF_OUTPUT_DIR]) / filename
        if path.is_file():
            duration = recorder.wav_duration(path)

    cancel_timer: Any = None
    event_remove: Any = None

    @callback
    def _restore(*_args) -> None:
        nonlocal cancel_timer, event_remove
        if cancel_timer is not None:
            cancel_timer()
            cancel_timer = None
        if event_remove is not None:
            event_remove()
            event_remove = None
        state = hass.states.get(player_id)
        if state is None or state.state in (
            MediaPlayerState.OFF,
            MediaPlayerState.UNAVAILABLE,
        ):
            return
        if old_volume is not None:
            hass.async_create_task(
                hass.services.async_call(
                    "media_player",
                    "volume_set",
                    {"entity_id": player_id, "volume_level": old_volume},
                    blocking=True,
                )
            )
        hass.async_create_task(
            hass.services.async_call(
                "media_player", "play", {"entity_id": player_id}, blocking=True
            )
        )
        _LOGGER.debug("Restored volume/playback on %s", player_id)

    @callback
    def _on_state_change(_entity_id, _old_state, new_state) -> None:
        if new_state is None:
            return
        if new_state.state in (
            MediaPlayerState.IDLE,
            MediaPlayerState.OFF,
            MediaPlayerState.PAUSED,
        ):
            _restore()

    event_remove = event.async_track_state_change(
        hass, player_id, None, _on_state_change
    )

    timeout = (duration or 0) + _RESTORE_FALLBACK_EXTRA_SECONDS
    cancel_timer = event.async_call_later(hass, timeout, lambda _now: _restore())

    @callback
    def _cleanup_only() -> None:
        nonlocal cancel_timer, event_remove
        if cancel_timer is not None:
            cancel_timer()
            cancel_timer = None
        if event_remove is not None:
            event_remove()
            event_remove = None

    event.async_call_later(hass, _RESTORE_TIMEOUT_SECONDS, lambda _now: _cleanup_only())
