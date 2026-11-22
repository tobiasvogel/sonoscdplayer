"""Bounded, temporary handoff of cover bytes to the private player service.

Only the player may approve a disc binding and commit a permanent cover. The
web server does not decode images or accept a caller-selected filesystem path.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import time

from sonoscdplayer.metadata.manual_cover import MAX_UPLOAD_BYTES, valid_upload_id

MAX_STAGED_FILES = 8
MAX_STAGE_AGE = 3600


def stage_upload(stream, length, state_dir):
    """Read the bounded raw HTTP body into a private, unguessable temp file."""
    if type(length) is not int or not 0 < length <= MAX_UPLOAD_BYTES:
        raise ValueError('invalid upload size')
    directory = Path(state_dir) / 'cover-staging'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    existing = 0
    for entry in directory.iterdir():
        if not (entry.name.endswith('.upload') and valid_upload_id(entry.name[:-7])):
            continue
        info = entry.lstat()
        if not stat.S_ISREG(info.st_mode) or now - info.st_mtime > MAX_STAGE_AGE:
            entry.unlink(missing_ok=True)
        else:
            existing += 1
    if existing >= MAX_STAGED_FILES:
        raise ValueError('too many pending cover uploads')
    upload_id = secrets.token_hex(16)
    path = directory / (upload_id + '.upload')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as target:
            remaining = length
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    raise ValueError('incomplete upload')
                target.write(chunk)
                remaining -= len(chunk)
        return upload_id
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def discard_upload(state_dir, upload_id):
    if valid_upload_id(upload_id):
        (Path(state_dir) / 'cover-staging' / (upload_id + '.upload')).unlink(missing_ok=True)
