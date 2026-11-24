"""Media source that exposes saved voice broadcast recordings."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
    generate_media_source_id,
)
from homeassistant.core import HomeAssistant

from . import recorder
from .const import CONF_OUTPUT_DIR, DOMAIN, MEDIA_VIEW_URL

_LOGGER = logging.getLogger(__name__)


class VoiceBroadcastMediaSource(MediaSource):
    """Expose the recording directory as a media source."""

    name = "Voice Broadcast"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(DOMAIN)
        self.hass = hass
        self.output_dir: Path | None = None

    def _set_output_dir(self) -> Path | None:
        for cfg in self.hass.data.get(DOMAIN, {}).values():
            self.output_dir = Path(cfg[CONF_OUTPUT_DIR])
            break
        return self.output_dir

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the recordings, newest first."""
        output_dir = self._set_output_dir()
        if output_dir is None:
            raise Unresolvable
        files = await self.hass.async_add_executor_job(recorder.directory_files, output_dir)
        children = [
            BrowseMediaSource(
                media_class="music",
                media_content_type="music",
                media_content_id=generate_media_source_id(DOMAIN, file.name),
                name=file.stem,
            )
            for file in files
        ]
        return BrowseMediaSource(
            media_class="music",
            media_content_type="music",
            media_content_id=generate_media_source_id(DOMAIN, ""),
            name="Voice Broadcast",
            can_play=False,
            children=children,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a recording to a playable URL."""
        output_dir = self._set_output_dir()
        if output_dir is None or item.identifier is None:
            raise Unresolvable
        filename = Path(item.identifier).name
        path = output_dir / filename
        if not path.is_file() or not filename.endswith(".wav"):
            raise Unresolvable
        # Relative URL: media player integrations prefix it with the HA host.
        url = MEDIA_VIEW_URL.format(filename=filename)
        return PlayMedia(url, "audio/wav", path=path)



async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Return the Voice Broadcast media source."""
    return VoiceBroadcastMediaSource(hass)
