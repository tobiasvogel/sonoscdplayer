"""Single-resolution, file-based Cover Art Archive cache.

Pillow is optional: absent Pillow affects covers only, never CD playback.
No upscale; dimensions are bounded by max_dimension_px on both axes.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

DEFAULT_COVER_DIR = Path('/var/lib/sonoscdplayer/covers')
_UUID = re.compile(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')


DEFAULT_BUDGET_MB = 100


class CoverBudgetFull(Exception):
    """The cover store reached its budget; nothing is deleted."""


@dataclass(frozen=True)
class CoverPolicy:
    enabled: bool = False
    max_dimension_px: int = 512
    jpeg_quality: int = 85
    budget_mb: int = DEFAULT_BUDGET_MB

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('cover enabled must be boolean')
        if type(self.max_dimension_px) is not int or not 64 <= self.max_dimension_px <= 2048:
            raise ValueError('cover max_dimension_px must be 64..2048')
        if type(self.jpeg_quality) is not int or not 40 <= self.jpeg_quality <= 95:
            raise ValueError('cover jpeg_quality must be 40..95')
        if type(self.budget_mb) is not int or not 1 <= self.budget_mb <= 10000:
            raise ValueError('cover budget_mb must be 1..10000')


class CoverCache:
    def __init__(self, directory=DEFAULT_COVER_DIR):
        self.directory = Path(directory)

    def _path(self, release_id: str) -> Path:
        if not _UUID.fullmatch(release_id):
            raise ValueError('invalid MusicBrainz release ID')
        return self.directory / (sha256(release_id.lower().encode()).hexdigest() + '.jpg')

    def used_bytes(self) -> int:
        """Size of all stored covers."""
        try:
            return sum(entry.stat().st_size for entry in self.directory.glob('*.jpg')
                       if entry.is_file())
        except OSError:
            return 0

    def find(self, release_id: str) -> Path | None:
        path = self._path(release_id)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def get_or_download(self, release_id: str, *, policy=CoverPolicy(),
                        opener=urllib.request.urlopen, timeout=8) -> Path | None:
        """Single bounded request. Caller must run outside the playback path."""
        existing = self.find(release_id)
        if existing is not None:
            return existing
        if not policy.enabled:
            return None
        if not 1 <= timeout <= 30:
            raise ValueError('timeout must be 1..30 seconds')
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise RuntimeError('Pillow is needed only for cover downloads') from exc
        url = 'https://coverartarchive.org/release/' + release_id + '/front'
        request = urllib.request.Request(
            url, headers={'User-Agent': 'SonosCDPlayer/0.1 (https://github.com/tobiasvogel/sonoscdplayer)'})
        try:
            with opener(request, timeout=timeout) as response:
                payload = response.read(8_000_001)
            if len(payload) > 8_000_000:
                return None
            with Image.open(BytesIO(payload)) as image:
                if image.width * image.height > 20_000_000:
                    return None
                image.load()
                image.thumbnail((policy.max_dimension_px, policy.max_dimension_px),
                                Image.Resampling.LANCZOS)
                rgb = image.convert('RGB')
                encoded = BytesIO()
                rgb.save(encoded, format='JPEG', quality=policy.jpeg_quality, optimize=True)
            if self.used_bytes() + len(encoded.getvalue()) > policy.budget_mb * 1024 * 1024:
                # Full budget: keep every stored cover, store no new one.
                raise CoverBudgetFull()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self._path(release_id)
            fd, temp_name = tempfile.mkstemp(prefix='.cover-', dir=self.directory)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(encoded.getvalue())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return path
        except (urllib.error.URLError, TimeoutError, OSError,
                UnidentifiedImageError, ValueError, Image.DecompressionBombError):
            return None
