"""Logic test for the Voice Broadcast keyword-cut / trim / save functions.

Runs without a Home Assistant install (stubs the few HA helpers that
recorder.py imports):

    python3 tests/test_recorder_logic.py
"""

import math
import os
import struct
import sys
import tempfile
import time
import types
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _stub_homeassistant() -> None:
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")

    class _HomeAssistant:
        """Placeholder."""

    core.HomeAssistant = _HomeAssistant
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    ulid = types.ModuleType("homeassistant.util.ulid")
    dt.now = lambda: datetime.now()
    dt.utcnow = lambda: datetime.now(timezone.utc)

    class _Counter:
        n = 0

    def _ulid():
        _Counter.n += 1
        return f"TESTULID{_Counter.n}"

    ulid.ulid = _ulid
    util.dt = dt
    util.ulid = ulid
    ha.core = core
    ha.util = util
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.core": core,
            "homeassistant.util": util,
            "homeassistant.util.dt": dt,
            "homeassistant.util.ulid": ulid,
        }
    )


def _load_recorder():
    pkg = types.ModuleType("voicebroadcast")
    pkg.__path__ = [str(ROOT / "custom_components" / "voicebroadcast")]
    sys.modules["voicebroadcast"] = pkg
    import importlib

    return importlib.import_module("voicebroadcast.recorder")


SR = 16000


def tone(freq: float, seconds: float, vol: float = 0.5) -> bytes:
    return b"".join(
        struct.pack("<h", int(vol * 32767 * math.sin(2 * math.pi * freq * i / SR)))
        for i in range(int(SR * seconds))
    )


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SR * seconds)


def main() -> None:
    _stub_homeassistant()
    recorder = _load_recorder()

    # "broadcast" (0.8s tone) + 0.4s pause + message (2.0s tone) + 0.5s tail
    pcm = tone(440, 0.8) + silence(0.4) + tone(660, 2.0) + silence(0.5)
    cut_s = recorder.find_cut_offset(pcm, SR) / SR / 2
    assert 0.3 <= cut_s <= 1.3, f"cut at {cut_s:.3f}s"
    print(f"cut offset: {cut_s:.3f}s (pause starts at 0.800s)")

    dur = len(recorder.trim_message(pcm[recorder.find_cut_offset(pcm, SR):], SR)) / SR / 2
    assert 1.9 <= dur <= 2.4, f"message duration {dur:.3f}s"
    print(f"message duration: {dur:.3f}s (tone: 2.0s)")

    pcm2 = tone(440, 0.8) + silence(0.6)
    cut2 = recorder.find_cut_offset(pcm2, SR)
    dur2 = len(recorder.trim_message(pcm2[cut2:], SR)) / SR / 2
    assert dur2 < recorder.MIN_MESSAGE_SECONDS
    print(f"keyword-only: {dur2:.3f}s -> discarded (min {recorder.MIN_MESSAGE_SECONDS}s)")

    # continuous tone, no gap anywhere -> assumed-word-end cut
    pcm3 = tone(440, 2.7)
    cut3_s = recorder.find_cut_offset(pcm3, SR) / SR / 2
    assert cut3_s == recorder._ASSUMED_WORD_S, f"assumed-word-end cut at {cut3_s:.3f}s"
    print(f"no-gap assumed word end: cut at {cut3_s:.3f}s")

    # syllable gap inside the keyword must NOT be the cut point:
    # "broad" (0.45s) + gap (40ms) + "cast" (0.35s) + pause (120ms) + message
    pcm6 = tone(440, 0.45) + silence(0.04) + tone(440, 0.35) + silence(0.12) + tone(660, 2.0)
    cut6_s = recorder.find_cut_offset(pcm6, SR) / SR / 2
    assert 0.78 <= cut6_s <= 0.90, f"syllable-gap cut at {cut6_s:.3f}s (real pause at 0.84s)"
    print(f"syllable gap ignored: cut at {cut6_s:.3f}s (word ends at 0.840s)")

    # lead-in before the word, no gap after it: cut must land past the word
    # (0.3s lead-in + 0.6s word -> word ends at 0.9s)
    pcm7 = silence(0.3) + tone(440, 0.6) + tone(660, 2.0)
    cut7_s = recorder.find_cut_offset(pcm7, SR) / SR / 2
    assert 1.3 <= cut7_s <= 1.5 and cut7_s > 0.9, f"lead-in no-gap cut at {cut7_s:.3f}s"
    print(f"lead-in, no gap: cut at {cut7_s:.3f}s (word ends at 0.900s)")

    # enhancer-style raised noise floor: edge trim must still work
    pcm8 = tone(660, 0.5, vol=0.05) + tone(660, 1.5, vol=0.5) + tone(660, 0.5, vol=0.05)
    dur8 = len(recorder.trim_message(pcm8, SR)) / SR / 2
    assert dur8 < 2.1, f"noise-floor trim left {dur8:.2f}s of a 2.5s clip"
    print(f"noise-floor trim: {dur8:.2f}s kept from 2.5s (voice is 1.5s)")

    # plain silence edges still trim to the natural margins
    pcm9 = silence(0.4) + tone(660, 2.0) + silence(0.5)
    dur9 = len(recorder.trim_message(pcm9, SR)) / SR / 2
    assert 2.25 <= dur9 <= 2.45, f"silence trim left {dur9:.2f}s (expect ~2.37s)"
    print(f"silence trim: {dur9:.2f}s kept from 2.9s (voice is 2.0s)")

    # very short 40 ms gap right after the keyword -> cut at the gap
    pcm4 = tone(440, 0.7) + silence(0.04) + tone(660, 2.0)
    cut4_s = recorder.find_cut_offset(pcm4, SR) / SR / 2
    assert 0.65 <= cut4_s <= 0.78, f"short-gap cut at {cut4_s:.3f}s"
    print(f"short-gap (40ms): cut at {cut4_s:.3f}s")

    # no silence, but a low-energy breath dip after the keyword
    pcm5 = tone(440, 0.7) + tone(660, 0.15, vol=0.03) + tone(660, 2.0)
    cut5_s = recorder.find_cut_offset(pcm5, SR) / SR / 2
    assert 0.65 <= cut5_s <= 0.78, f"breath-dip cut at {cut5_s:.3f}s"
    print(f"breath dip: cut at {cut5_s:.3f}s")

    assert recorder.is_broadcast("Broadcast dinner is ready", {"broadcast"})
    assert recorder.is_broadcast("broadcast, dinner is ready", {"broadcast"})
    assert not recorder.is_broadcast("play the broadcast album", {"broadcast"})
    assert not recorder.is_broadcast("", {"broadcast"})
    assert recorder.first_word("  Broadcast! ") == "broadcast"
    assert recorder.first_word("turn on the light") == "turn"
    print("keyword detection + first_word: OK")

    # odd-length PCM (truncated stream) must not crash the energy profile
    odd_pcm = pcm[: len(pcm) - 1]
    recorder._energy_profile(odd_pcm, SR)
    cut_odd = recorder.find_cut_offset(odd_pcm, SR)
    assert cut_odd % 2 == 0
    print("odd-length PCM: OK")

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        rec = recorder._save_sync(out_dir, pcm, SR, 24)
        assert rec is not None
        with wave.open(str(rec.path), "rb") as w:
            assert abs(w.getnframes() / w.getframerate() - rec.duration) < 0.01
            assert w.getsampwidth() == 2 and w.getnchannels() == 1
        assert len(recorder.directory_files(out_dir)) == 1
        old = out_dir / "old.wav"
        with wave.open(str(old), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(silence(0.5))
        os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))
        recorder._cleanup_sync(out_dir, 24)
        assert not old.exists() and rec.path.exists()
        print(f"save/read/cleanup: OK ({rec.file}, {rec.duration:.2f}s)")

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
