# Voice Broadcast for Home Assistant

Say **"broadcast <message>"** to Assist and hear **your own voice** on your
other speakers — instead of a TTS robot re-reading your words.

Works with any voice assistant device that sends audio to Assist:
Home Assistant Voice Preview Edition, View Assist / View Assist
Companion App, ESPHome voice devices, the Assist button in the
browser, etc.

## How it works

```
"Okay Nabu, broadcast dinner is at seven"
        │
        ▼
  Assist (wake word already stripped)
        │  raw 16 kHz mono PCM
        ▼
  Voice Broadcast (speech-to-text, this integration)
        ├── tee ───────────► your normal speech-to-text engine (e.g. Whisper)
        │                          │
        │                          ▼ transcript
        │               first word == "broadcast"?
        │                 no  ───► transcript returned unchanged
        │                          (everything downstream is normal)
        │                 yes ──► audio saved as WAV (keyword stripped)
        │                          voicebroadcast.recorded event
        ▼
  Voice Broadcast Assistant (conversation agent)
        ├── "broadcast ..." ─► short confirmation (default "Sent.")
        │                      auto-broadcast (source device excluded)
        └── anything else ───► delegated to the assistant you configured
        ▼
  Broadcast targets
        ├── Sonos / Squeezebox / B&O   ─► native announce (pause + resume)
        ├── Voice assistant devices    ─► announced with the recording
        └── everything else            ─► volume duck → play → restore
```

Nothing is sent to the cloud, no extra service is running, and no TTS
voice is ever involved in a broadcast.

## Install

### Via Marketplace (recommended)

1. Open **Settings → Marketplace** and click **Add from link** (top of
   the page).
2. Paste the **GitHub link** `https://github.com/heyitsbcs/ha-voicebroadcast`
   and pick *Integration* as what it adds. Home Assistant will ask you
   to connect your GitHub account once.
3. Find **Voice Broadcast** in the Marketplace and install it.
4. Restart Home Assistant.

*On older Home Assistant versions the Marketplace is called **HACS**:
Menu → Custom → Custom Repositories → + Add, enter
`heyitsbcs/ha-voicebroadcast`, category *Integration*, then
Custom → Integrations → **Voice Broadcast** → Download.*

### Manual

1. Copy the `custom_components/voicebroadcast` folder into your
   Home Assistant `config` directory:
   `config/custom_components/voicebroadcast/`
2. Restart Home Assistant.

No `requirements` — the integration uses only the standard library and
existing HA APIs.

## Setup

1. **Settings → Devices & services → Integrations → Add integration → Voice Broadcast**
2. Fill in the form:
   - **Upstream speech-to-text engine** — the speech-to-text entity
     your assistant currently uses (e.g. `stt.whisper`). This is what
     actually transcribes your words; Voice Broadcast just sits in
     front of it.
   - **Trigger word** — default `broadcast`. Say it as the *first word*
     after the wake word.
   - **Extra trigger words** — optional comma-separated aliases.
   - **Assistant** — the agent that handles *normal* voice commands
     (everything that is not a broadcast). Select your usual
     assistant here (e.g. your LLM agent) so normal commands behave
     exactly as before. Leave empty for the built-in Home Assistant
     agent.
   - **Recording folder** — where WAVs are stored (default
     `voicebroadcast` under the config directory).
   - **Broadcast automatically** — play new recordings immediately
     (recommended).
   - **Broadcast targets** and **Voice satellites** — these two lists
     together define where broadcasts go: **only the listed devices**
     receive them. An empty list means no devices of that kind, and if
     both are empty the recording is saved but played nowhere (a
     warning is logged). A device that exists in both lists (e.g.
     ESPHome) plays only once, via the voice-assistant route.
   - **Confirmation** — the short phrase spoken on the source voice
     assistant after a broadcast (default `Sent.`; leave empty for
     silence).
   - **Chime before satellite announcement** — play the device's
     preannounce chime before the recording (on by default).
   - **Duck volume** — volume set while the announcement plays on
     players that don't support native announce (default `0.3`).
   - **Max recording duration** / **Delete recordings older than** —
     sane defaults provided.
3. In **Settings → Voice assistants → Assistants**, open the assistant
   your device uses from the **Assist** list:
   - **Speech-to-text**: select **Voice Broadcast**
   - **Conversation agent**: select **Voice Broadcast Assistant**
   - Leave **Text-to-speech** as it was.

   ⚠️ **Both must be set on the assistant each voice device actually
   uses.** Each voice assistant device can run its *own* assistant —
   check (or change) it under **Settings → Voice assistants →
   Assistants → Assist devices**, in the **Assistant** column. If your
   device runs a different assistant, set both fields on *that* one
   instead. Without the conversation agent, the device answers your
   broadcast with a robot TTS reply instead of just "Sent.".

That's it. Every voice assistant device that runs that assistant now
supports broadcasts; all other commands behave exactly as before —
they go to the conversation agent you selected, not to a different one.

All settings can be changed later under **Settings → Devices &
services → Integrations → Voice Broadcast → Configure**; the
integration reloads itself and picks up changes immediately.

## Usage

> "Okay Nabu, **broadcast** — dinner is at seven, don't be late!"

- You hear a short **"Sent."** from the source voice assistant
  (configurable or silent); the recording itself plays on the other
  media players and voice assistant devices.
- The trigger word is stripped from the recording with an energy-based
  cut (the first significant energy dip after the word; if you leave no
  gap, the quietest point right after the word is used), then
  leading/trailing silence is trimmed.

### Manual / scripted broadcast

The `voicebroadcast.broadcast` service plays any media source or URL on
any media player (with announce support or ducking) and any voice
assistant device (announced with the recording as media):

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

Recordings are also browsable under **Voice Broadcast** in the
**Browse media** picker (music note) on any media player card.

## Notes & limitations

- **Trigger word is first-word only.** "Play the song *Broadcast*" will
  not fire a broadcast; it passes through to your normal
  speech-to-text engine and conversation agent.
- **The source is never a target.** The voice assistant device you
  speak the broadcast from never plays its own message back — not via
  the voice-assistant route and not via a media player entity on the
  same device. Manual `voicebroadcast.broadcast` service calls are
  unaffected (they play everywhere you name).
- **Latency.** A broadcast is ready when the end of your sentence is
  detected (voice activity detection); normal commands are unaffected
  (audio is teed live to your speech-to-text engine).
- **Announce support.** Only players with the `MEDIA_ANNOUNCE` feature
  (Sonos, Squeezebox, B&O, …) get pause-and-resume. Everything else gets
  ducked volume, restored when playback ends (state listener + a
  duration-based fallback timer). Voice assistant devices are
  announced via `assist_satellite.announce` (HA 2026.11+ removed them
  from the media player domain); they play the recording directly and,
  by default, their chime before it.
- **Quality.** The recording is whatever your device's mic captured,
  processed by Assist's audio enhancements — great for "your voice",
  not studio quality.
- **WAV format.** Recordings are 16-bit mono WAV. Most players handle
  this fine; if one of yours doesn't, tell me and I'll add an optional
  `ffmpeg` transcode step.
- **Multiple assistants.** Set up a *separate* Voice Broadcast
  instance per assistant if you need different trigger words or targets
  per device (upstream + trigger word determine the entry's identity).

## License

MIT — see [LICENSE](LICENSE).
