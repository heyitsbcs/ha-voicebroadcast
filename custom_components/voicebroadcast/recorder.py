"""Record, trim, and manage voice broadcast recordings.
The pipeline hands us a stream of 16-bit mono PCM (the pipeline's VAD
already trims the stream to the spoken command). The upstream STT engine
only returns text without word timestamps, so the leading trigger word is
stripped with a small energy detector: the word's start is located from the
first sustained voiced run (the stream can carry a short lead-in), and the
first real pause after the word marks where the message starts. Short dips
are the gaps *inside* the keyword ("broad|cast") and are ignored. If the
speaker leaves no gap at all, the keyword is assumed to end 1.1 s after it
started. Leading/trailing silence is trimmed against an adaptive noise
floor so the pipeline's audio enhancer cannot defeat the trim.
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
_SILENCE_RUN = 3  # consecutive low-energy windows (30 ms) mark a boundary
_VOICE_RUN = 5  # consecutive voiced windows (50 ms) mark the word's start
_SPEECH_SEARCH_S = 3.0  # the word starts within the first 3 s
_GATE_RATIO = 0.30  # a dip below 30% of the loudest window is a boundary
_PEAK_WINDOW_S = 2.0  # calibrate the gate on the keyword, not the message
_WORD_MIN_MS = 600  # ignore dips within 600 ms of the word (syllable gaps)
_DIP_SEARCH_S = 2.0  # the word-end gap is within 2 s of the word's start
_ASSUMED_WORD_S = 1.1  # no detectable gap: the keyword runs this long
_KEEP_LEADING_MS = 120
_KEEP_TRAILING_MS = 250
_ABS_GATE = 80  # RMS floor below which audio is considered silence
_NOISE_PERCENTILE = 30  # low-energy windows estimate the noise floor
_NOISE_MARGIN = 2.5
_TRIM_PEAK_RATIO = 0.10


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
    if len(pcm) % 2:
        pcm = pcm[:-1]
    total_windows = max(1, int((len(pcm) / SAMPLE_WIDTH / sample_rate) * (1000 / _WINDOW_MS)))
    step = max(1, sample_rate // 2000)
    samples = array("h")
    samples.frombytes(pcm)
    downsampled = samples[::step]
    per_window = max(1, len(downsampled) // total_windows)
    profile: list[float] = []
    for i in range(0, len(downsampled), per_window):
        chunk = downsampled[i : i + per_window]
        if not chunk:
            continue
        profile.append((sum(s * s for s in chunk) / len(chunk)) ** 0.5)
    return profile


def _trim_gate(profile: list[float]) -> float:
    """Silence gate for edge trimming, adapted to the noise floor.
    The pipeline's audio enhancer leaves a raised noise floor (often
    10-25% of the loudest window), so a pure peak-relative gate would
    never trim anything. Estimate the floor from the low-energy windows.
    """
    ordered = sorted(profile)
    noise = ordered[max(0, int(len(ordered) * _NOISE_PERCENTILE / 100) - 1)]
    return max(_ABS_GATE, noise * _NOISE_MARGIN, max(profile) * _TRIM_PEAK_RATIO)


def _find_speech_start(profile: list[float], gate: float) -> int:
    """Return the index of the first sustained voiced run (the word's start)."""
    limit = min(int(_SPEECH_SEARCH_S * (1000 // _WINDOW_MS)), len(profile))
    run = 0
    for i in range(limit):
        if profile[i] >= gate:
            run += 1
            if run >= _VOICE_RUN:
                return i - run + 1
        else:
            run = 0
    # No sustained run found; anchor on the loudest window.
    return profile.index(max(profile))

def find_cut_offset(pcm: bytes, sample_rate: int) -> int:
    """Return the byte offset just after the leading trigger word."""
    if len(pcm) < SAMPLE_WIDTH * 10:
        return 0
    profile = _energy_profile(pcm, sample_rate)
    if not profile or max(profile) < _ABS_GATE:
        return 0
    # Calibrate the gate on the keyword region: the message that follows
    # can be much louder and would push the gate above the keyword's
    # own energy.
    peak_windows = max(1, int(_PEAK_WINDOW_S * (1000 // _WINDOW_MS)))
    peak = max(profile[:peak_windows])
    gate = max(_ABS_GATE, peak * _GATE_RATIO)
    windows_per_second = 1000 // _WINDOW_MS

    # Where does the word actually start? The stream can carry a short
    # lead-in (breath, wake-word residue, an untranscribed filler), so all
    # offsets below are relative to the detected speech start, not t=0.
    speech_start = _find_speech_start(profile, gate)

    # First real pause after at least _WORD_MIN_MS of voiced word: shorter
    # dips are the gaps *inside* the keyword (e.g. "broad|cast").
    min_index = speech_start + int(_WORD_MIN_MS / _WINDOW_MS)
    max_index = min(
        speech_start + int(_DIP_SEARCH_S * windows_per_second),
        len(profile),
    )
    run = 0
    for i in range(min_index, max_index):
        if profile[i] < gate:
            run += 1
            if run >= _SILENCE_RUN:
                cut_seconds = (i - run + 1) * _WINDOW_MS / 1000
                return int(cut_seconds * sample_rate) * SAMPLE_WIDTH
        else:
            run = 0

    # No detectable gap: assume the keyword ends _ASSUMED_WORD_S after it
    # started (covers slow, careful enunciation).
    cut_seconds = speech_start / windows_per_second + _ASSUMED_WORD_S
    return int(cut_seconds * sample_rate) * SAMPLE_WIDTH


def trim_message(pcm: bytes, sample_rate: int) -> bytes:
    """Trim leading/trailing silence, keeping small natural margins."""
    if len(pcm) < SAMPLE_WIDTH * 10:
        return b""
    profile = _energy_profile(pcm, sample_rate)
    if not profile:
        return b""
    gate = _trim_gate(profile)
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

    filename = f"{dt_util.now():%Y%m%d-%H%M%S}-{ulid_util.ulid()}.wav"
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
