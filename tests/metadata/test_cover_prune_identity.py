"""Cover pruning refuses files replaced after selection (WEB-07b2d)."""
import os

import pytest

from sonoscdplayer.metadata import library_maintenance
from test_library_maintenance import setup, CONFIRM_COVERS


def test_prune_refuses_replaced_inode(tmp_path, monkeypatch):
    maintenance, _tokens, _db, covers = setup(tmp_path)
    victim = covers / ('a' * 64 + '.jpg')
    original_open = os.open
    swapped = False

    def change_before_directory_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and os.fspath(path) == os.fspath(covers):
            swapped = True
            victim.unlink()
            victim.write_bytes(b'replacement')
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(library_maintenance.os, 'open', change_before_directory_open)
    with pytest.raises(ValueError, match='cover changed'):
        maintenance.apply(action='prune-auto', confirmation=CONFIRM_COVERS)
    assert victim.read_bytes() == b'replacement'


def test_prune_refuses_symlink_swap(tmp_path, monkeypatch):
    maintenance, _tokens, _db, covers = setup(tmp_path)
    victim = covers / ('a' * 64 + '.jpg')
    protected = covers / ('manual-' + 'f' * 32 + '.jpg')
    protected.write_bytes(b'protected')
    original_open = os.open
    swapped = False

    def change_before_directory_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and os.fspath(path) == os.fspath(covers):
            swapped = True
            victim.unlink()
            victim.symlink_to(protected)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(library_maintenance.os, 'open', change_before_directory_open)
    with pytest.raises(ValueError, match='cover changed'):
        maintenance.apply(action='prune-auto', confirmation=CONFIRM_COVERS)
    assert victim.is_symlink()
    assert protected.read_bytes() == b'protected'
