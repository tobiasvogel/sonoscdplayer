"""Remember the display language chosen in the menu.

The OLED service owns ``ui.json`` in the state directory:
``{"language": "de", "version": 1}``. It takes precedence over ``[ui]
language`` in ``ui.toml``. The C shutdown programs read the same value to
pick their frame set, so the file stays this small and flat.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import stat

from sonoscdplayer.system.state_files import default_state_dir, read_json, write_json, MAX_BYTES
from sonoscdplayer.ui.i18n.translator import available_languages

FILE_NAME = 'ui.json'
VERSION = 1
LOCK_NAME = '.ui-language.lock'


class LanguageConflict(ValueError):
    """The device menu or another web client changed the saved language."""


@contextmanager
def _write_lock(directory):
    """Coordinate UI and web writes across processes, not only HTTP threads."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / LOCK_NAME, os.O_CREAT | os.O_RDWR |
                 getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('invalid language lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _revision(path):
    """A bounded opaque token for the complete saved file, including invalid JSON."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return 'none'
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise OSError('invalid language state file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise OSError('invalid language state file')
        return sha256(data).hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)


class UiState:
    """The menu language chosen on the device, kept in the state directory."""
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

    def revision(self):
        """Opaque version of the existing file; no mutation or device RPC."""
        return _revision(self.path)

    def save_language(self, language):
        """Remember a local menu selection using the same lock as web writes."""
        try:
            with _write_lock(self.path.parent):
                if self.path.is_symlink():
                    raise OSError('invalid language state path')
                return write_json(self.path, {'version': VERSION, 'language': language}, log=self._log)
        except OSError:
            return False

    def save_if_revision(self, language, revision):
        """Stage a web choice unless the on-device menu has changed the file."""
        if (type(language) is not str or language not in available_languages() or
                type(revision) is not str or
                revision != 'none' and (len(revision) != 64 or
                any(c not in '0123456789abcdef' for c in revision))):
            raise ValueError('invalid language settings request')
        with _write_lock(self.path.parent):
            if self.revision() != revision:
                raise LanguageConflict('saved language changed')
            if self.path.is_symlink():
                raise OSError('invalid language state path')
            if not write_json(self.path, {'version': VERSION, 'language': language}, log=lambda *_: None):
                raise OSError('language state not saved')
            return self.revision()
