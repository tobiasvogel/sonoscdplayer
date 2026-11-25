"""Persistent preferences for the web client; never alter /etc configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading


THEMES = frozenset({'auto', 'light', 'dark'})


class WebPreferences:
    """Read and atomically update web.json in the service state directory."""

    def __init__(self, directory: str | Path):
        self.path = Path(directory) / 'web.json'
        self._lock = threading.Lock()

    def read(self) -> str:
        """Return the saved theme, or auto for absent/invalid state."""
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                mode = data['appearance']['theme']
                return mode if isinstance(mode, str) and mode in THEMES else 'auto'
            except (OSError, ValueError, KeyError, TypeError):
                return 'auto'

    def set_theme(self, theme: str) -> None:
        """Write only a validated theme, atomically and mode 0600."""
        if not isinstance(theme, str) or theme not in THEMES:
            raise ValueError('theme must be auto, light, or dark')
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix='.web-', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump({'appearance': {'theme': theme}}, stream, separators=(',', ':'))
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self.path)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
