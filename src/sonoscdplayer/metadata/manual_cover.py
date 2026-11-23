"""WEB-03c: disc-qualified manual covers; only the metadata executor writes files.

The HTTP service stages opaque, short-lived bytes. The player authorizes an
upload for the currently inserted TOC; Pillow decodes and bounds it off-thread.
Manual covers are distinct from release-shared Cover Art Archive images.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import Lock

from sonoscdplayer.metadata.cover_store import CoverBudgetFull, CoverStore, DEFAULT_COVER_DIR
from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from sonoscdplayer.metadata.manual_edits import disc_token

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 20_000_000
DEFAULT_STAGING = Path('/var/lib/sonoscdplayer/cover-staging')
_UPLOAD_ID = re.compile(r'^[0-9a-f]{32}$')


def valid_upload_id(value):
    return type(value) is str and _UPLOAD_ID.fullmatch(value) is not None


class ManualCoverStore:
    """One private JPEG per complete CD identity, not per MusicBrainz release."""

    def __init__(self, cover_dir=DEFAULT_COVER_DIR, staging_dir=DEFAULT_STAGING,
                 ui_path=None):
        self.cover_dir = Path(cover_dir)
        self.staging_dir = Path(staging_dir)
        self.preferences = read_cover_preferences(ui_path)

    def path(self, identity):
        return self.cover_dir / ('manual-' + disc_token(identity) + '.jpg')

    def find(self, identity):
        path = self.path(identity)
        try:
            return path if path.is_file() and path.stat().st_size > 0 else None
        except OSError:
            return None

    def _jpeg(self, payload):
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            raise ValueError('image decoder unavailable') from exc
        if not 0 < len(payload) <= MAX_UPLOAD_BYTES:
            raise ValueError('invalid cover size')
        try:
            with Image.open(BytesIO(payload)) as img:
                if img.format not in ('JPEG', 'PNG') or img.width * img.height > MAX_PIXELS:
                    raise ValueError('invalid cover image')
                img.load()
                oriented = ImageOps.exif_transpose(img)
                oriented.thumbnail((self.preferences.max_dimension_px,
                                    self.preferences.max_dimension_px), Image.Resampling.LANCZOS)
                rgb = oriented.convert('RGB')
                result = BytesIO()
                rgb.save(result, format='JPEG', quality=self.preferences.jpeg_quality,
                         optimize=True)
                return result.getvalue()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('invalid cover image') from exc

    def save(self, identity, upload_id, *, current, guarded_replace=None):
        """Consume the staged file exactly once, even on error or stale CD."""
        if not valid_upload_id(upload_id):
            raise ValueError('invalid upload identifier')
        staged = self.staging_dir / (upload_id + '.upload')
        try:
            fd = os.open(staged, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_UPLOAD_BYTES:
                    raise ValueError('invalid staged cover')
                data = source.read(MAX_UPLOAD_BYTES + 1)
            encoded = self._jpeg(data)
            if not current():
                raise ValueError('disc changed')
            path = self.path(identity)
            existing_size = path.stat().st_size if path.is_file() else 0
            budget = self.preferences.budget_mb * 1024 * 1024
            if (CoverStore(self.cover_dir).used_bytes() - existing_size + len(encoded) > budget):
                raise CoverBudgetFull('cover storage budget reached')
            self.cover_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temp_name = tempfile.mkstemp(prefix='.manual-cover-', dir=self.cover_dir)
            try:
                with os.fdopen(fd, 'wb') as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                if guarded_replace is not None:
                    guarded_replace(lambda: os.replace(temp_name, path))
                else:
                    if not current():
                        raise ValueError('disc changed')
                    os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return str(path)
        finally:
            staged.unlink(missing_ok=True)

    def remove(self, identity, *, current, guarded_remove=None):
        def action():
            self.path(identity).unlink(missing_ok=True)
        if guarded_remove is not None:
            guarded_remove(action)
        else:
            if not current():
                raise ValueError('disc changed')
            action()
        return None


class ManualCoverWorkflow:
    """Methods invoked by the single serialized player; file I/O is async."""

    def _init_manual_covers(self, cover_dir, ui_path, staging_dir=DEFAULT_STAGING):
        self._manual_covers = ManualCoverStore(cover_dir, staging_dir, ui_path)
        self._cover_pending = None
        self._cover_path = None
        self._cover_seq = 0
        self._cover_error = None
        self._cover_session = 0
        self._cover_gate = Lock()

    def _reset_manual_covers(self):
        with self._cover_gate:
            self._cover_session += 1
        self._cover_path = None
        self._cover_error = None
        self._cover_seq += 1

    def _cover_identity(self, token):
        if type(token) is not str or not _UPLOAD_ID.fullmatch(token):
            raise ValueError('invalid disc token')
        if self._last_toc is None or self._result is None:
            raise ValueError('no identified disc ready')
        identity = self._identify(self._last_toc)
        if disc_token(identity) != token or self._result.get('edit_token') != token:
            raise ValueError('disc changed; reload metadata')
        return identity

    def enqueue_cover(self, *, token, upload_id=None, remove=False):
        identity = self._cover_identity(token)
        if type(remove) is not bool or (remove == (upload_id is not None)):
            raise ValueError('select upload or removal')
        if upload_id is not None and not valid_upload_id(upload_id):
            raise ValueError('invalid upload identifier')
        if self._cover_pending is not None:
            raise RuntimeError('cover operation already pending')
        session = self._cover_session
        def current():
            return self._cover_session == session
        def work():
            guarded = lambda action: self._cover_commit(session, action)
            if remove:
                return self._manual_covers.remove(identity, current=current, guarded_remove=guarded)
            return self._manual_covers.save(identity, upload_id, current=current, guarded_replace=guarded)
        self._cover_pending = (token, self._executor.submit(work))

    def _cover_commit(self, session, action):
        with self._cover_gate:
            if self._cover_session != session:
                raise ValueError('disc changed')
            return action()

    def _poll_manual_covers(self):
        if self._cover_pending is None or not self._cover_pending[1].done():
            return False
        token, future = self._cover_pending
        self._cover_pending = None
        # Never apply an old CD's completion to the newly inserted disc.
        if self._result is None or self._result.get('edit_token') != token:
            return False
        try:
            self._cover_path = future.result()
            self._cover_error = None
        except CoverBudgetFull:
            self._cover_error = 'metadata.error.cover_budget_full'
        except Exception:
            self._cover_error = 'metadata.error.cover_save_failed'
        self._cover_seq += 1
        return True

    def _manual_cover_snapshot(self, data):
        if data is None:
            return None
        if self._cover_path is not None:
            data['cover_file'] = self._cover_path
            data['cover_status'] = 'manual'
        data['cover_manual'] = self._cover_path is not None
        data['cover_seq'] = self._cover_seq
        data['cover_error'] = self._cover_error
        return data
