"""Transparent STT router that records broadcast utterances.

Every utterance is forwarded to the configured upstream STT engine and
the transcript is passed through unchanged, so normal voice commands are
completely unaffected. When the first word of the transcript is the
trigger word (default: "broadcast"), the raw audio (minus the trigger
word) is saved as a WAV recording and an event is fired. The conversation
agent (which knows the speaking satellite) then broadcasts the recording
to the configured targets, excluding the satellite it was spoken on.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import recorder
from .const import (
    CONF_KEEP_HOURS,
    CONF_MAX_DURATION,
    CONF_OUTPUT_DIR,
    CONF_UPSTREAM,
    DOMAIN,
    EVENT_RECORDED,
)

_LOGGER = logging.getLogger(__name__)


class _QueueStream:
    """Async iterable feeding a queue to the upstream STT engine."""

    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        chunk = await self.queue.get()
        if chunk is None:
            raise StopAsyncIteration
        return chunk


class VoiceBroadcastSTT(stt.SpeechToTextEntity):
    """STT entity that routes audio to the upstream engine and records."""

    _attr_name = "Voice Broadcast"

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize."""
        self.hass = hass
        self.config = config

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.config["entry_id"]

    @property
    def supported_languages(self) -> list[str]:
        """Return the supported languages (mirrors the upstream engine)."""
        return list(self.config["languages"])

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        """Return the supported formats."""
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        """Return the supported codecs."""
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        """Return the supported bit rates."""
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        """Return the supported sample rates."""
        return [
            stt.AudioSampleRates.SAMPLERATE_16000,
            stt.AudioSampleRates.SAMPLERATE_44100,
            stt.AudioSampleRates.SAMPLERATE_48000,
        ]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        """Return the supported channels."""
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self,
        metadata: stt.SpeechMetadata,
        stream: AsyncIterable[bytes],
    ) -> stt.SpeechResult:
        """Forward audio to the upstream engine; record on trigger word."""
        upstream = stt.async_get_speech_to_text_entity(self.hass, self.config[CONF_UPSTREAM])
        if upstream is None:
            _LOGGER.error(
                "Upstream STT entity %s is not available", self.config[CONF_UPSTREAM]
            )
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        keywords = self.config["keywords"]
        max_bytes = self.config[CONF_MAX_DURATION] * int(metadata.sample_rate) * 2
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        upstream_task = asyncio.ensure_future(
            upstream.internal_async_process_audio_stream(metadata, _QueueStream(queue))
        )

        buffer = bytearray()
        try:
            async for chunk in stream:
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    _LOGGER.warning(
                        "Broadcast recording truncated at %ss", self.config[CONF_MAX_DURATION]
                    )
                    break
                await queue.put(chunk)
        except asyncio.CancelledError:
            await queue.put(None)
            upstream_task.cancel()
            raise

        await queue.put(None)
        result = await upstream_task
        if result.result != stt.SpeechResultState.SUCCESS or not result.text:
            return result

        if not recorder.is_broadcast(result.text, keywords):
            return result

        _LOGGER.info("Broadcast utterance detected, saving recording")
        sample_rate = int(metadata.sample_rate)
        recording = await recorder.async_save_recording(
            self.hass,
            Path(self.config[CONF_OUTPUT_DIR]),
            bytes(buffer),
            sample_rate,
            self.config[CONF_KEEP_HOURS],
        )
        if recording is not None:
            data = {
                "file": recording.file,
                "path": str(recording.path),
                "duration": recording.duration,
                "sample_rate": recording.sample_rate,
                "media_source_id": f"media-source://{DOMAIN}/{recording.file}",
            }
            self.hass.bus.async_fire(EVENT_RECORDED, data)
            # Hand the recording to the conversation agent: it runs later in
            # the same pipeline run and knows which satellite the utterance
            # came from, so it can exclude the source from the targets.
            self.config["last_recording"] = {**data, "ts": time.monotonic()}
        return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the STT router."""
    config = hass.data[DOMAIN][entry.entry_id]
    # Do not block the config entry on the upstream engine being ready:
    # right after a restart (e.g. after an upgrade) this integration can
    # be set up before the upstream STT entity exists, and raising
    # ConfigEntryNotReady here dropped the STT component on every
    # upgrade. The upstream is resolved per utterance instead
    # (async_process_audio_stream), which handles a missing engine.
    if stt.async_get_speech_to_text_entity(hass, config[CONF_UPSTREAM]) is None:
        _LOGGER.warning(
            "Upstream STT entity %s is not available yet; the Voice "
            "Broadcast STT will work once it is. If this persists, check",
            "the configured upstream speech-to-text engine.",
            config[CONF_UPSTREAM],
        )
    async_add_entities([VoiceBroadcastSTT(hass, config)])
