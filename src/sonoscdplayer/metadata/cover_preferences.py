"""The ``[ui.cover]`` settings in ui.toml: show covers, size, quality, budget.

Cover downloads are optional, never part of playback or CD-TEXT probing.
In auto mode an explicit caller-supplied display capability is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from sonoscdplayer.system.config_paths import ui_config
from sonoscdplayer.metadata.cover_store import DEFAULT_BUDGET_MB, CoverPolicy


@dataclass(frozen=True)
class CoverPreferences:
    """``mode`` auto/on/off, stored size, JPEG quality and disk budget (MB)."""
    mode: str = 'auto'
    max_dimension_px: int = 512
    jpeg_quality: int = 85
    budget_mb: int = DEFAULT_BUDGET_MB

    def __post_init__(self):
        if type(self.mode) is not str or self.mode not in ('auto', 'on', 'off'):
            raise ValueError('ui.cover.mode must be auto, on or off')
        # Reuse the same validation as the image pipeline.
        self._policy(False)

    def _policy(self, enabled):
        return CoverPolicy(enabled=enabled, max_dimension_px=self.max_dimension_px,
                           jpeg_quality=self.jpeg_quality, budget_mb=self.budget_mb)

    def download_policy(self, *, display_supports_cover: bool,
                        sonos_uses_cover: bool = False) -> CoverPolicy:
        """auto: download when the display or Sonos shows covers; off: never."""
        if type(display_supports_cover) is not bool or type(sonos_uses_cover) is not bool:
            raise ValueError('cover capability flags must be boolean')
        enabled = self.mode == 'on' or (
            self.mode == 'auto' and (display_supports_cover or sonos_uses_cover))
        return self._policy(enabled)


def read_cover_preferences(path: Path | None = None) -> CoverPreferences:
    """UI config is optional; missing file means auto with 512px limit."""
    filename = ui_config() if path is None else Path(path)
    try:
        with filename.open('rb') as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return CoverPreferences()
    if not isinstance(data, dict):
        raise ValueError('ui.toml must be a table')
    ui = data.get('ui', {})
    if not isinstance(ui, dict):
        raise ValueError('[ui] must be a table')
    cover = ui.get('cover', {})
    if not isinstance(cover, dict):
        raise ValueError('[ui.cover] must be a table')
    return CoverPreferences(mode=cover.get('mode', 'auto'),
                            max_dimension_px=cover.get('max_dimension_px', 512),
                            jpeg_quality=cover.get('jpeg_quality', 85),
                            budget_mb=cover.get('budget_mb', DEFAULT_BUDGET_MB))
