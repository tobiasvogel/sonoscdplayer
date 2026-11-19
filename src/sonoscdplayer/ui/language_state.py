"""Remember the display language chosen in the menu.

The OLED service owns ``ui.json`` in the state directory:
``{"language": "de", "version": 1}``. It takes precedence over ``[ui]
language`` in ``ui.toml``. The C shutdown programs read the same value to
pick their frame set, so the file stays this small and flat.
"""
from __future__ import annotations

from pathlib import Path

from sonoscdplayer.system.state_files import default_state_dir, read_json, write_json

FILE_NAME = 'ui.json'
VERSION = 1


class UiState:
    def __init__(self, directory=None, *, log=print):
        self.path = Path(directory if directory is not None else default_state_dir()) / FILE_NAME
        self._log = log

    def language(self, available):
        """Saved language if it still has a locale file, else None."""
        data = read_json(self.path, log=self._log)
        if not isinstance(data, dict) or data.get('version') != VERSION:
            return None
        language = data.get('language')
        return language if isinstance(language, str) and language in available else None

    def save_language(self, language):
        return write_json(self.path, {'version': VERSION, 'language': language}, log=self._log)
