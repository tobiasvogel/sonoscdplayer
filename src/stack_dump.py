"""Diagnostics: on SIGUSR1 write every thread's Python stack to a file.

For a hang in the field: `sudo pkill -USR1 -f track_ring_http.py` (or
player_service.py), then read /run/sonoscdplayer/stacks-*.txt. Nothing is
written unless the signal is sent. Contains code paths only, no PCM.
"""
import faulthandler
import os
import signal
import tempfile
from pathlib import Path

_files = []


def enable(name):
    """Register the handler; never fails the caller."""
    try:
        root = Path('/run/sonoscdplayer')
        if not (root.is_dir() and os.access(root, os.W_OK)):
            root = Path(tempfile.gettempdir())
        handle = open(root / f'stacks-{name}-{os.getpid()}.txt', 'w')
        _files.append(handle)            # must stay open for faulthandler
        faulthandler.register(signal.SIGUSR1, file=handle, all_threads=True, chain=False)
        return handle.name
    except Exception:
        return None
