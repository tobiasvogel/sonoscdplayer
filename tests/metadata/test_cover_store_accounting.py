"""Regression: disk budget counts legacy JPEGs; inventory lists managed covers only."""
import os

import pytest

from sonoscdplayer.metadata.cover_store import CoverStore
from sonoscdplayer.web.cover_storage import cover_inventory


def test_budget_includes_legacy_jpegs_but_inventory_does_not(tmp_path):
    auto = tmp_path / ('a' * 64 + '.jpg')
    manual = tmp_path / ('manual-' + 'b' * 32 + '.jpg')
    auto.write_bytes(b'a' * 17)
    manual.write_bytes(b'b' * 23)
    (tmp_path / 'unmanaged.jpg').write_bytes(b'x' * 400)
    (tmp_path / ('c' * 64 + '.jpg')).symlink_to(auto)
    assert CoverStore(tmp_path).used_bytes() == 440
    assert sum(item.size for item in cover_inventory(tmp_path)) == 40


def test_missing_directory_is_empty_but_invalid_directory_is_error(tmp_path):
    assert CoverStore(tmp_path / 'missing').used_bytes() == 0
    invalid = tmp_path / 'not-a-directory'
    invalid.write_text('x')
    with pytest.raises(NotADirectoryError):
        CoverStore(invalid).used_bytes()


def test_scan_error_is_not_mistaken_for_zero(monkeypatch, tmp_path):
    import sonoscdplayer.metadata.cover_store as module
    def denied(_path):
        raise PermissionError('scan denied')
    monkeypatch.setattr(module.os, 'scandir', denied)
    with pytest.raises(PermissionError):
        CoverStore(tmp_path).used_bytes()
