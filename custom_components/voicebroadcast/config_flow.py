"""Config flow for Voice Broadcast."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import stt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ALIASES,
    CONF_AUTO_BROADCAST,
    CONF_CONFIRM,
    CONF_DUCK_VOLUME,
    CONF_KEEP_HOURS,
    CONF_KEYWORD,
    CONF_LANGUAGES,
    CONF_MAX_DURATION,
    CONF_OUTPUT_DIR,
    CONF_PREANNOUNCE,
    CONF_SATELLITES,
    CONF_TARGETS,
    CONF_UPSTREAM,
    DEFAULT_AUTO_BROADCAST,
    DEFAULT_CONFIRM,
    DEFAULT_DUCK_VOLUME,
    DEFAULT_KEEP_HOURS,
    DEFAULT_KEYWORD,
    DEFAULT_LANGUAGES,
    DEFAULT_MAX_DURATION,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PREANNOUNCE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UPSTREAM): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="stt",
            )
        ),
        vol.Optional(CONF_KEYWORD, default=DEFAULT_KEYWORD): str,
        vol.Optional(CONF_ALIASES, default=""): str,
        vol.Optional(CONF_OUTPUT_DIR, default=DEFAULT_OUTPUT_DIR): str,
        vol.Optional(CONF_AUTO_BROADCAST, default=DEFAULT_AUTO_BROADCAST): bool,
        vol.Optional(CONF_CONFIRM, default=DEFAULT_CONFIRM): str,
        vol.Optional(CONF_TARGETS): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="media_player",
                multiple=True,
            )
        ),
        vol.Optional(CONF_SATELLITES): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="assist_satellite",
                multiple=True,
            )
        ),
        vol.Optional(CONF_PREANNOUNCE, default=DEFAULT_PREANNOUNCE): bool,
        vol.Optional(CONF_DUCK_VOLUME, default=DEFAULT_DUCK_VOLUME): vol.Coerce(float),
        vol.Optional(CONF_MAX_DURATION, default=DEFAULT_MAX_DURATION): vol.Coerce(int),
        vol.Optional(CONF_KEEP_HOURS, default=DEFAULT_KEEP_HOURS): vol.Coerce(int),
        vol.Optional(CONF_LANGUAGES, default=",".join(DEFAULT_LANGUAGES)): str,
    }
)


class VoiceBroadcastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Broadcast."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            upstream = user_input[CONF_UPSTREAM]
            stt_entity = stt.async_get_speech_to_text_entity(self.hass, upstream)
            if stt_entity is None:
                errors["base"] = "upstream_not_found"
            else:
                await self.async_set_unique_id(f"{DOMAIN}-{upstream}")
                self._abort_if_unique_id_configured()
                data = {
                    CONF_UPSTREAM: upstream,
                    CONF_KEYWORD: user_input[CONF_KEYWORD].strip().lower()
                    or DEFAULT_KEYWORD,
                    CONF_ALIASES: [
                        alias.strip().lower()
                        for alias in user_input[CONF_ALIASES].split(",")
                        if alias.strip()
                    ],
                    CONF_OUTPUT_DIR: user_input[CONF_OUTPUT_DIR].strip()
                    or DEFAULT_OUTPUT_DIR,
                    CONF_AUTO_BROADCAST: user_input[CONF_AUTO_BROADCAST],
                    CONF_CONFIRM: user_input[CONF_CONFIRM].strip(),
                    CONF_TARGETS: list(user_input.get(CONF_TARGETS) or []),
                    CONF_SATELLITES: list(user_input.get(CONF_SATELLITES) or []),
                    CONF_PREANNOUNCE: user_input[CONF_PREANNOUNCE],
                    CONF_DUCK_VOLUME: user_input[CONF_DUCK_VOLUME],
                    CONF_MAX_DURATION: user_input[CONF_MAX_DURATION],
                    CONF_KEEP_HOURS: user_input[CONF_KEEP_HOURS],
                    CONF_LANGUAGES: [
                        lang.strip()
                        for lang in user_input[CONF_LANGUAGES].split(",")
                        if lang.strip()
                    ],
                }
                return self.async_create_entry(title="Voice Broadcast", data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
