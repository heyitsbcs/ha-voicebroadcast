"""Constants for the Voice Broadcast integration."""

DOMAIN = "voicebroadcast"

EVENT_RECORDED = "voicebroadcast.recorded"

# Config entry keys
CONF_UPSTREAM = "upstream"
CONF_KEYWORD = "keyword"
CONF_ALIASES = "aliases"
CONF_OUTPUT_DIR = "output_dir"
CONF_AGENT = "agent"
CONF_AUTO_BROADCAST = "auto_broadcast"
CONF_TARGETS = "targets"
CONF_CONFIRM = "confirm"
CONF_SATELLITES = "satellites"
CONF_PREANNOUNCE = "preannounce"
CONF_DUCK_VOLUME = "duck_volume"
CONF_MAX_DURATION = "max_duration"
CONF_KEEP_HOURS = "keep_hours"
CONF_LANGUAGES = "languages"

DEFAULT_KEYWORD = "broadcast"
DEFAULT_OUTPUT_DIR = "voicebroadcast"
DEFAULT_AUTO_BROADCAST = True
DEFAULT_DUCK_VOLUME = 0.3
DEFAULT_MAX_DURATION = 30
DEFAULT_CONFIRM = "Sent."
DEFAULT_PREANNOUNCE = True
DEFAULT_KEEP_HOURS = 24
DEFAULT_LANGUAGES = ["en-US"]

MIN_MESSAGE_SECONDS = 0.35
SAMPLE_WIDTH = 2  # 16-bit PCM
SATELLITE_DOMAIN = "assist_satellite"

SERVICE_BROADCAST = "broadcast"
SERVICE_CLEAR = "clear"

MEDIA_VIEW_URL = "/api/voicebroadcast/{filename}"
