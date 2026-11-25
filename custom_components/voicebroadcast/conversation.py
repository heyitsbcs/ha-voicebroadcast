"""Conversation agent: short confirmation on broadcasts, transparent otherwise.

This agent is selected as the pipeline's conversation agent. Utterances
that start with the trigger word get a short spoken confirmation (by
default "Sent." — set an empty confirmation to stay fully silent) so
no long robot reply is spoken, and triggers the broadcast of the
recording the STT router just saved -- to all configured targets
EXCEPT the satellite the utterance was spoken on, which never plays
back its own message. Everything else is delegated to the
configured pass-through assistant (by default the built-in Home
Assistant agent, e.g. your LLM agent), which keeps all normal voice
commands working exactly as before.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import recorder
from .const import CONF_AGENT, CONF_AUTO_BROADCAST, CONF_CONFIRM, DOMAIN

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
            _LOGGER.debug("Broadcast utterance: confirming + broadcasting")
            self._maybe_auto_broadcast(user_input)
            response = intent.IntentResponse(
                language=user_input.language or "en"
            )
            confirm = self.config.get(CONF_CONFIRM, "").strip()
            if confirm:
                # Short spoken confirmation; an empty confirmation keeps
                # the response silent (the pipeline skips TTS entirely).
                response.async_set_speech(confirm)
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        result = await conversation.async_converse(
            hass=self.hass,
            text=text,
            conversation_id=user_input.conversation_id,
            context=user_input.context,
            language=user_input.language,
            # agent_id=None -> the built-in Home Assistant agent; the
            # configured pass-through assistant (e.g. an LLM agent)
            # otherwise keeps handling normal voice commands
            agent_id=self.config.get(CONF_AGENT) or None,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=user_input.extra_system_prompt,
        )
        return conversation.ConversationResult(
            response=result.response,
            conversation_id=result.conversation_id,
        )


    # The recording is saved by the STT stage of the SAME pipeline run,
    # which finished before this agent runs; anything older belongs to a
    # previous utterance (a typed "broadcast ..." has no recording at all).
    _RECORDING_TTL_S = 10.0

    def _maybe_auto_broadcast(
        self, user_input: conversation.ConversationInput
    ) -> None:
        """Broadcast the fresh recording, excluding the source satellite."""
        if not self.config.get(CONF_AUTO_BROADCAST, True):
            return
        recording = self.config.get("last_recording")
        if not recording or (
            time.monotonic() - recording.get("ts", 0) > self._RECORDING_TTL_S
        ):
            _LOGGER.debug("No fresh recording available; not broadcasting")
            return
        # Fire and forget: delivery failures must never break the pipeline
        # (the recording itself is already saved and can be replayed via
        # the voicebroadcast.broadcast service).
        self.hass.async_create_task(
            self._async_auto_broadcast(recording, user_input.satellite_id)
        )

    async def _async_auto_broadcast(
        self, recording: dict[str, Any], exclude_satellite: str | None
    ) -> None:
        """Broadcast the new recording to the configured targets."""
        from . import broadcast

        try:
            played = await broadcast.async_broadcast(
                self.hass,
                self.config,
                media_source_id=recording["media_source_id"],
                exclude_satellite=exclude_satellite,
            )
            _LOGGER.info("Broadcast played on: %s", played or "(no players)")
        except Exception:  # noqa: BLE001 - never break the voice pipeline
            _LOGGER.exception("Auto-broadcast failed")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation agent."""
    config = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VoiceBroadcastAgent(hass, config)])
