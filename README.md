# Voice Broadcast for Home Assistant

Say **"broadcast <message>"** to Assist and hear **your own voice** on your
other speakers — instead of a TTS robot re-reading your words.

Works with any Assist client that feeds audio into the Assist pipeline:
Home Assistant Voice Preview Edition, View Assist / companion app
satellites, ESPHome satellites, the browser Assist panel, etc.

## How it works

```
"Okay Nabu, broadcast dinner is at seven"
        │
        ▼
  Assist pipeline (wake word already stripped)
        │  raw 16 kHz mono PCM
        ▼
  Voice Broadcast STT (this integration)
        ├── tee ───────────► your normal STT engine (e.g. Whisper)
        │                          │
        │                          ▼ transcript
        │               first word == "broadcast"?
        │                 no  ───► transcript returned unchanged
        │                          (everything downstream is normal)
        │                 yes ──► audio saved as WAV (keyword stripped)
        │                          voicebroadcast.recorded event
        │                          auto-broadcast
        ▼
  Voice Broadcast Assistant (conversation agent)
        ├── "broadcast ..." ─► silent (no robot reply)
        └── anything else ───► delegated to the default HA agent
        ▼
  Media players
        ├── Sonos / Squeezebox / B&O  ─► native announce (pause + resume)
        └── everything else            ─► volume duck → play → restore
```

Nothing is sent to the cloud, no extra service is running, and no TTS
voice is ever involved in a broadcast.

## Install

1. Copy the `custom_components/voicebroadcast` folder into your
   Home Assistant `config` directory:
   `config/custom_components/voicebroadcast/`
2. Restart Home Assistant.

No `requirements` — the integration uses only the standard library and
existing HA APIs.

## Setup

1. **Settings → Devices & Services → Add Integration → Voice Broadcast**
2. Fill in the form:
   - **Upstream speech-to-text engine** — the STT entity your pipeline
     currently uses (e.g. `stt.whisper`). This is what actually
     transcribes your words; Voice Broadcast just sits in front of it.
   - **Trigger word** — default `broadcast`. Say it as the *first word*
     after the wake word.
   - **Extra trigger words** — optional comma-separated aliases.
   - **Recording folder** — where WAVs are stored (default
     `voicebroadcast` under the config directory).
   - **Broadcast automatically** — play new recordings immediately
     (recommended).
   - **Broadcast targets** — specific media players, or leave empty for
     all of them.
   - **Duck volume** — volume set while the announcement plays on
     players that don't support native announce (default `0.3`).
   - **Max recording duration** / **Delete recordings older than** —
     sane defaults provided.
3. Open **Settings → Voice & Talk → Assist pipeline** (the one your
   satellite uses):
   - **Speech-to-text**: select **Voice Broadcast**
   - **Assistant**: select **Voice Broadcast Assistant**
   - Leave **Text-to-speech** as it was.

That's it. Every device on that pipeline now supports broadcasts; all
other commands behave exactly as before.

## Usage

> "Okay Nabu, **broadcast** — dinner is at seven, don't be late!"

- You hear nothing back from the satellite (silent confirmation; the
  recording is played on the other players).
- The trigger word is stripped from the recording with an energy-based
  cut (first sustained silence after the word), then leading/trailing
  silence is trimmed.

### Manual / scripted broadcast

The `voicebroadcast.broadcast` service plays any media source or URL on
any media player (with announce support or ducking):

```yaml
service: voicebroadcast.broadcast
data:
  media_source_id: media-source://voicebroadcast/20261124-120000-01J....wav
target:
  entity_id:
    - media_player.kitchen
```

`voicebroadcast.clear` deletes all stored recordings.

### Automations

The `voicebroadcast.recorded` event fires with the new recording's
details (`file`, `path`, `duration`, `media_source_id`) — handy for
"notify me", logging, or broadcast-only-on-conditions:

```yaml
automation:
  - trigger:
      - event:
          event_type: voicebroadcast.recorded
    action:
      - service: notify.mobile_app_phone
        data:
          message: >
            Broadcast recorded ({{ trigger.event.data.duration | round(1) }}s)
```

Recordings are also browsable under **Voice Broadcast** in the media
browser.

## Notes & limitations

- **Trigger word is first-word only.** "Play the song *Broadcast*" will
  not fire a broadcast; it passes through to your normal STT/agent.
- **Latency.** A broadcast is ready when the pipeline's VAD ends your
  sentence; normal commands are unaffected (audio is teed live to your
  STT engine).
- **Announce support.** Only players with the `MEDIA_ANNOUNCE` feature
  (Sonos, Squeezebox, B&O, …) get pause-and-resume. Everything else gets
  ducked volume, restored when playback ends (state listener + a
  duration-based fallback timer).
- **Quality.** The recording is whatever your satellite's mic captured,
  processed by the pipeline's audio enhancements — great for "your
  voice", not studio quality.
- **WAV format.** Recordings are 16-bit mono WAV. Most players handle
  this fine; if one of yours doesn't, tell me and I'll add an optional
  `ffmpeg` transcode step.
- **Multiple pipelines.** Set up a *separate* Voice Broadcast instance
  per pipeline if you need different trigger words or targets per
  device (upstream + trigger word determine the entry's identity).
