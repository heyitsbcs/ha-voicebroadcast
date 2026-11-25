"""Config flow for Voice Broadcast."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import stt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALIASES,
    CONF_AGENT,
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


def _as_text(value: object) -> str:
    """Render a stored list/str form value as comma-separated text."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value or "")


def _build_schema(current: dict[str, object]) -> vol.Schema:
    """Build the form schema, pre-filling `current` values as defaults."""
    return vol.Schema(
        {
            vol.Required(CONF_UPSTREAM): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="stt",
                )
            ),
            vol.Optional(
                CONF_KEYWORD,
                default=str(current.get(CONF_KEYWORD, DEFAULT_KEYWORD)),
            ): str,
            vol.Optional(CONF_ALIASES, default=_as_text(current.get(CONF_ALIASES))): str,
            vol.Optional(
                CONF_OUTPUT_DIR,
                default=str(current.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)),
            ): str,
            vol.Optional(
                CONF_AUTO_BROADCAST,
                default=bool(current.get(CONF_AUTO_BROADCAST, DEFAULT_AUTO_BROADCAST)),
            ): bool,
            vol.Optional(
                CONF_AGENT, default=current.get(CONF_AGENT) or None
            ): selector.ConversationAgentSelector(),
            vol.Optional(
                CONF_CONFIRM, default=str(current.get(CONF_CONFIRM, DEFAULT_CONFIRM))
            ): str,
            vol.Optional(
                CONF_TARGETS, default=list(current.get(CONF_TARGETS) or [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="media_player",
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_SATELLITES, default=list(current.get(CONF_SATELLITES) or [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="assist_satellite",
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_PREANNOUNCE,
                default=bool(current.get(CONF_PREANNOUNCE, DEFAULT_PREANNOUNCE)),
            ): bool,
            vol.Optional(
                CONF_DUCK_VOLUME,
                default=current.get(CONF_DUCK_VOLUME, DEFAULT_DUCK_VOLUME),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_MAX_DURATION,
                default=current.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION),
            ): vol.Coerce(int),
            vol.Optional(
                CONF_KEEP_HOURS,
                default=current.get(CONF_KEEP_HOURS, DEFAULT_KEEP_HOURS),
            ): vol.Coerce(int),
            vol.Optional(
                CONF_LANGUAGES,
                default=_as_text(current.get(CONF_LANGUAGES, DEFAULT_LANGUAGES)),
            ): str,
        }
    )


def _extract_data(user_input: dict[str, object]) -> dict[str, object]:
    """Validate and normalize form data (shared by user and options steps).

    CONF_AGENT is None when left empty; the built-in Home Assistant agent
    is used as the pass-through in that case.
    """
    return {
        CONF_UPSTREAM: user_input[CONF_UPSTREAM],
        CONF_KEYWORD: str(user_input[CONF_KEYWORD]).strip().lower() or DEFAULT_KEYWORD,
        CONF_ALIASES: [
            alias.strip().lower()
            for alias in str(user_input.get(CONF_ALIASES) or "").split(",")
            if alias.strip()
        ],
        CONF_OUTPUT_DIR: str(user_input[CONF_OUTPUT_DIR]).strip() or DEFAULT_OUTPUT_DIR,
        CONF_AUTO_BROADCAST: bool(user_input[CONF_AUTO_BROADCAST]),
        CONF_AGENT: user_input.get(CONF_AGENT) or None,
        CONF_CONFIRM: str(user_input[CONF_CONFIRM]).strip(),
        CONF_TARGETS: list(user_input.get(CONF_TARGETS) or []),
        CONF_SATELLITES: list(user_input.get(CONF_SATELLITES) or []),
        CONF_PREANNOUNCE: bool(user_input[CONF_PREANNOUNCE]),
        CONF_DUCK_VOLUME: user_input[CONF_DUCK_VOLUME],
        CONF_MAX_DURATION: user_input[CONF_MAX_DURATION],
        CONF_KEEP_HOURS: user_input[CONF_KEEP_HOURS],
        CONF_LANGUAGES: [
            lang.strip()
            for lang in str(user_input.get(CONF_LANGUAGES) or "").split(",")
            if lang.strip()
        ],
    }


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
                return self.async_create_entry(
                    title="Voice Broadcast", data=_extract_data(user_input)
                )

        return self.async_show_form(
            step_id="user", data_schema=_build_schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> VoiceBroadcastOptionsFlow:
        """Get the options flow for this handler."""
        return VoiceBroadcastOptionsFlow(config_entry)


def _suggested_values(current: dict[str, object]) -> dict[str, object]:
    """Current values, shaped the way the form displays them."""
    return {
        CONF_UPSTREAM: current.get(CONF_UPSTREAM),
        CONF_KEYWORD: str(current.get(CONF_KEYWORD, DEFAULT_KEYWORD)),
        CONF_ALIASES: _as_text(current.get(CONF_ALIASES)),
        CONF_OUTPUT_DIR: str(current.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)),
        CONF_AUTO_BROADCAST: bool(current.get(CONF_AUTO_BROADCAST, DEFAULT_AUTO_BROADCAST)),
        CONF_AGENT: current.get(CONF_AGENT),
        CONF_CONFIRM: str(current.get(CONF_CONFIRM, DEFAULT_CONFIRM)),
        CONF_TARGETS: list(current.get(CONF_TARGETS) or []),
        CONF_SATELLITES: list(current.get(CONF_SATELLITES) or []),
        CONF_PREANNOUNCE: bool(current.get(CONF_PREANNOUNCE, DEFAULT_PREANNOUNCE)),
        CONF_DUCK_VOLUME: current.get(CONF_DUCK_VOLUME, DEFAULT_DUCK_VOLUME),
        CONF_MAX_DURATION: current.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION),
        CONF_KEEP_HOURS: current.get(CONF_KEEP_HOURS, DEFAULT_KEEP_HOURS),
        CONF_LANGUAGES: _as_text(current.get(CONF_LANGUAGES, DEFAULT_LANGUAGES)),
    }


class VoiceBroadcastOptionsFlow(OptionsFlow):
    """Edit the settings of an existing Voice Broadcast entry."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle the options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=_extract_data(user_input))

        current: dict[str, object] = dict(self._entry.data)
        current.update(self._entry.options or {})
        suggested = _suggested_values(current)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _build_schema(current), suggested
            ),
        )
