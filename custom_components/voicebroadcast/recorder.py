"""Record, trim, and manage voice broadcast recordings.

The pipeline hands us a stream of 16-bit mono PCM (the pipeline's VAD
already trims the stream to the spoken command). The upstream STT engine
only returns text without word timestamps, so the leading trigger word is
stripped with a small energy detector: the first sustained silence gap
after a minimum word length marks the end of the keyword.
"""

from __future__ import annotations

import logging
import re
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util import ulid as ulid_util

from .const import (
    DEFAULT_KEEP_HOURS,
    MIN_MESSAGE_SECONDS,
    SAMPLE_WIDTH,
)

_LOGGER = logging.getLogger(__name__)

_WINDOW_MS = 10
_SILENCE_RUN = 8  # consecutive quiet windows (80 ms) end the keyword
_MIN_CUT_MS = 300  # never cut inside the first 300 ms
_MAX_SCAN_S = 3.0
_FALLBACK_CUT_S = 0.75
_KEEP_LEADING_MS = 120
_KEEP_TRAILING_MS = 250
_ABS_GATE = 80  # RMS floor below which audio is considered silence


@dataclass(frozen=True)
class Recording:
    """A saved broadcast recording."""

    file: str
    path: Path
    duration: float
    sample_rate: int


def keyword_set(keyword: str, aliases: list[str]) -> set[str]:
    """Build the set of trigger words."""
    words = {keyword.strip().lower()}
    for alias in aliases:
        word = alias.strip().lower()
        if word:
            words.add(word)
    return words


def first_word(text: str) -> str:
    """Return the first word of text, lowercased and stripped of punctuation."""
    match = re.match(r"[a-z]+", text.strip().lower())
    return match.group(0) if match else ""


def is_broadcast(text: str, keywords: set[str]) -> bool:
    """Return True if the utterance starts with a broadcast trigger word."""
    return first_word(text) in keywords


def _energy_profile(pcm: bytes, sample_rate: int) -> list[float]:
    """Return RMS per 10 ms window, downsampled for speed."""
    total_windows = max(1, int((len(pcm) / SAMPLE_WIDTH / sample_rate) * (1000 / _WINDOW_MS)))
    step = max(1, sample_rate // 2000)
    samples = array("h")
    samples.frombytes(pcm[::step])
    per_window = max(1, len(samples) // total_windows)
    profile: list[float] = []
    for i in range(0, len(samples), per_window):
        chunk = samples[i : i + per_window]
        if not chunk:
            continue
        profile.append((sum(s * s for s in chunk) / len(chunk)) ** 0.5)
    return profile


def _gate(profile: list[float]) -> float:
    return max(_ABS_GATE, max(profile) * 0.08)


def find_cut_offset(pcm: bytes, sample_rate: int) -> int:
    """Return the byte offset just after the leading trigger word."""
    if len(pcm) < SAMPLE_WIDTH * 10:
        return 0
    profile = _energy_profile(pcm, sample_rate)
    if not profile or max(profile) < _ABS_GATE:
        return 0
    gate = _gate(profile)
    windows_per_second = 1000 // _WINDOW_MS
    min_index = int(_MIN_CUT_MS / _WINDOW_MS)
    max_index = int(min(_MAX_SCAN_S, len(pcm) / SAMPLE_WIDTH / sample_rate) * windows_per_second)
    run = 0
    for i in range(min_index, min(max_index, len(profile))):
        if profile[i] < gate:
            run += 1
            if run >= _SILENCE_RUN:
                cut_seconds = (i - run + 1) * _WINDOW_MS / 1000
                return int(cut_seconds * sample_rate) * SAMPLE_WIDTH
        else:
            run = 0
    return int(_FALLBACK_CUT_S * sample_rate) * SAMPLE_WIDTH


def trim_message(pcm: bytes, sample_rate: int) -> bytes:
    """Trim leading/trailing silence, keeping small natural margins."""
    if len(pcm) < SAMPLE_WIDTH * 10:
        return b""
    profile = _energy_profile(pcm, sample_rate)
    if not profile:
        return b""
    gate = _gate(profile)
    voice = [i for i, rms in enumerate(profile) if rms >= gate]
    if not voice:
        return b""
    first, last = voice[0], voice[-1]
    start = max(0, first - _KEEP_LEADING_MS // _WINDOW_MS)
    end = min(len(profile), last + 1 + _KEEP_TRAILING_MS // _WINDOW_MS)
    windows_per_second = 1000 // _WINDOW_MS
    start_bytes = int(start / windows_per_second * sample_rate) * SAMPLE_WIDTH
    end_bytes = min(len(pcm), int(end / windows_per_second * sample_rate) * SAMPLE_WIDTH)
    return pcm[start_bytes:end_bytes]


def _cleanup_sync(directory: Path, keep_hours: int, keep: frozenset[str] = frozenset()) -> None:
    if not directory.is_dir():
        return
    now_ts = dt_util.utcnow().timestamp()
    for file in directory.glob("*.wav"):
        if file.name in keep:
            continue
        try:
            if now_ts - file.stat().st_mtime > keep_hours * 3600:
                file.unlink(missing_ok=True)
                _LOGGER.debug("Removed old broadcast recording %s", file.name)
        except OSError:
            _LOGGER.exception("Could not remove %s", file.name)


def _save_sync(
    directory: Path, pcm: bytes, sample_rate: int, keep_hours: int
) -> Recording | None:
    """Cut the keyword, trim silence, and write a WAV file."""
    cut = find_cut_offset(pcm, sample_rate)
    message = trim_message(pcm[cut:], sample_rate)
    duration = len(message) / SAMPLE_WIDTH / sample_rate
    if duration < MIN_MESSAGE_SECONDS:
        _LOGGER.debug("Broadcast recording discarded: message too short (%.2fs)", duration)
        return None

    filename = f"{dt_util.now():%Y%m%d-%H%M%S}-{ulid_util.new_ulid()}.wav"
    path = directory / filename
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(message)

    _cleanup_sync(directory, keep_hours, keep=frozenset({filename}))
    _LOGGER.info("Saved broadcast recording %s (%.1fs)", filename, duration)
    return Recording(file=filename, path=path, duration=duration, sample_rate=sample_rate)


async def async_save_recording(
    hass: HomeAssistant,
    output_dir: Path,
    pcm: bytes,
    sample_rate: int,
    keep_hours: int = DEFAULT_KEEP_HOURS,
) -> Recording | None:
    """Save a recording in the executor thread."""
    return await hass.async_add_executor_job(
        _save_sync, output_dir, pcm, sample_rate, keep_hours
    )


async def async_clear_recordings(hass: HomeAssistant, output_dir: Path) -> int:
    """Remove all recordings; returns the number of removed files."""

    def _clear() -> int:
        if not directory.is_dir():
            return 0
        count = 0
        for file in directory_files(directory):
            file.unlink(missing_ok=True)
            count += 1
        return count

    return await hass.async_add_executor_job(_clear)


def directory_files(directory: Path) -> list[Path]:
    """Return the WAV files in the recording directory, newest first."""
    if not directory.is_dir():
        return []
    return sorted(
        (f for f in directory.glob("*.wav") if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )


def wav_duration(path: Path) -> float | None:
    """Return the duration of a WAV file, or None if it cannot be read."""
    try:
        with wave.open(str(path), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except (wave.Error, OSError, ZeroDivisionError):
        return None
