"""Shared cover display preference and storage dimension (ui.toml).

Cover downloads are optional, never part of playback or CD-TEXT probing.
In auto mode an explicit caller-supplied display capability is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from config_paths import ui_config
from disc_covers import CoverPolicy


@dataclass(frozen=True)
class CoverPreferences:
    mode: str = 'auto'
    max_dimension_px: int = 512

    def __post_init__(self):
        if type(self.mode) is not str or self.mode not in ('auto', 'on', 'off'):
            raise ValueError('ui.cover.mode must be auto, on or off')
        # Reuse the same dimension validation as the image pipeline.
        CoverPolicy(enabled=False, max_dimension_px=self.max_dimension_px)

    def download_policy(self, *, display_supports_cover: bool) -> CoverPolicy:
        if type(display_supports_cover) is not bool:
            raise ValueError('display_supports_cover must be boolean')
        enabled = self.mode == 'on' or (self.mode == 'auto' and display_supports_cover)
        return CoverPolicy(enabled=enabled, max_dimension_px=self.max_dimension_px)


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
                            max_dimension_px=cover.get('max_dimension_px', 512))
