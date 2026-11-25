"""Conversation agent: silent on broadcasts, transparent otherwise.

This agent is selected as the pipeline's conversation agent. Utterances
that start with the trigger word get an empty response (so no robot
reply is spoken); everything else is delegated to the built-in
Home Assistant agent, which keeps all normal voice commands working
exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import recorder
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class VoiceBroadcastAgent(conversation.ConversationEntity):
    """Conversation entity that intercepts broadcast utterances."""

    _attr_name = "Voice Broadcast Assistant"

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize."""
        self.hass = hass
        self.config = config

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self.config['entry_id']}-agent"

    @property
    def supported_languages(self) -> list[str]:
        """Return the supported languages."""
        return list(self.config["languages"])

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process the utterance."""
        text = (user_input.text or "").strip()

        if text and recorder.is_broadcast(text, self.config["keywords"]):
            _LOGGER.debug("Broadcast utterance: staying silent")
            # Empty speech dict -> the pipeline skips the TTS stage
            # entirely, so the satellite stays silent.
            return conversation.ConversationResult(
                response=intent.IntentResponse(
                    language=user_input.language or "en"
                ),
                conversation_id=user_input.conversation_id,
            )

        result = await conversation.async_converse(
            hass=self.hass,
            text=text,
            conversation_id=user_input.conversation_id,
            context=user_input.context,
            language=user_input.language,
            # agent_id=None -> the built-in Home Assistant agent
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=user_input.extra_system_prompt,
        )
        return conversation.ConversationResult(
            response=result.response,
            conversation_id=result.conversation_id,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation agent."""
    config = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VoiceBroadcastAgent(hass, config)])
