"""Media source that exposes saved voice broadcast recordings."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from pathlib import Path

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
        self.output_dir: str | None = None

    def _output_dir(self) -> str | None:
        for cfg in self.hass.data.get(DOMAIN, {}).values():
            self.output_dir = cfg[CONF_OUTPUT_DIR]
            break
        return self.output_dir

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the recordings, newest first."""
        output_dir = self._output_dir()
        if output_dir is None:
            raise Unresolvable
        files = await self.hass.async_add_executor_job(
            recorder.directory_files, output_dir
        )
        children = [
            BrowseMediaSource(
                domain=self.domain,
                identifier=file.name,
                media_class=MediaClass.MUSIC,
                media_content_type="audio/wav",
                title=file.stem,
                can_play=True,
                can_expand=False,
            )
            for file in files
        ]
        return BrowseMediaSource(
            domain=self.domain,
            identifier="",
            media_class=MediaClass.MUSIC,
            media_content_type=None,
            title=self.name,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a recording to a playable URL."""
        output_dir = self._output_dir()
        if output_dir is None or not item.identifier:
            raise Unresolvable
        filename = item.identifier.split("/")[-1]
        path = Path(output_dir) / filename
        if not filename.endswith(".wav") or not path.is_file():
            raise Unresolvable
        # Relative URL: media player integrations prefix it with the HA host.
        url = MEDIA_VIEW_URL.format(filename=filename)
        return PlayMedia(url, "audio/wav", path=path)


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Return the Voice Broadcast media source."""
    return VoiceBroadcastMediaSource(hass)
