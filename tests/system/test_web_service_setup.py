"""The local web service is an explicit, standalone setup choice."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'install_runtime_services_web', ROOT / 'tools/install_runtime_services.py')
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def setup_files(tmp_path):
    rendered = tmp_path / 'rendered'
    rendered.mkdir()
    units = tmp_path / 'units'
    rules = tmp_path / 'polkit'
    target = tmp_path / installer.TARGET
    target.write_text('[Unit]\nDescription=Target\n', encoding='utf-8')
    for name in installer.UNITS[:4]:
        (rendered / name).write_text('[Unit]\nDescription=Core\n', encoding='utf-8')
    (rendered / installer.WEB).write_text(
        '[Unit]\nDescription=Web\n', encoding='utf-8')
    return rendered, units, rules, target


def test_web_unit_is_not_implicitly_selected(setup_files):
    rendered, units, rules, target = setup_files
    files = installer.plan(rendered, units, rules, target_unit=target)
    assert installer.WEB not in {dest.name for _, dest in files}


def test_complete_install_can_opt_in_to_web(setup_files):
    rendered, units, rules, target = setup_files
    files = installer.plan(rendered, units, rules, target_unit=target, web=True)
    assert installer.WEB in {dest.name for _, dest in files}
    assert installer.TARGET in {dest.name for _, dest in files}


def test_web_only_does_not_inspect_or_change_core_units(setup_files):
    rendered, units, rules, target = setup_files
    units.mkdir()
    (units / installer.UNITS[0]).write_text('custom installation', encoding='utf-8')
    selected = installer.plan(rendered, units, rules, web_only=True)
    assert [dest.name for _, dest in selected] == [installer.WEB]
    assert (units / installer.UNITS[0]).read_text(encoding='utf-8') == 'custom installation'


def test_web_only_rejects_other_units(setup_files):
    rendered, units, rules, target = setup_files
    with pytest.raises(ValueError, match='web-only'):
        installer.plan(rendered, units, rules, target_unit=target, web_only=True)
    with pytest.raises(ValueError, match='web-only'):
        installer.plan(rendered, units, rules, web=True, web_only=True)


def test_requested_web_must_exist(setup_files):
    rendered, units, rules, target = setup_files
    (rendered / installer.WEB).unlink()
    with pytest.raises(ValueError, match='Missing required rendered unit'):
        installer.plan(rendered, units, rules, web_only=True)
    with pytest.raises(ValueError, match='Missing required rendered unit'):
        installer.plan(rendered, units, rules, target_unit=target, web=True)


def test_existing_web_unit_or_dropin_requires_review(setup_files):
    rendered, units, rules, _ = setup_files
    units.mkdir()
    web = units / installer.WEB
    web.write_text('another installation', encoding='utf-8')
    with pytest.raises(ValueError, match='differs'):
        installer.plan(rendered, units, rules, web_only=True)
    web.write_bytes((rendered / installer.WEB).read_bytes())
    (units / (installer.WEB + '.d')).mkdir()
    (units / (installer.WEB + '.d') / 'override.conf').write_text('override')
    with pytest.raises(ValueError, match='drop-ins'):
        installer.plan(rendered, units, rules, web_only=True)


def test_web_only_preview_never_writes_files(setup_files):
    rendered, units, rules, _ = setup_files
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/install_runtime_services.py'),
         '--rendered-dir', str(rendered), '--unit-dir', str(units),
         '--polkit-dir', str(rules), '--web-only'],
        capture_output=True, text=True, check=True)
    assert 'Preview only' in result.stdout
    assert not units.exists()


def test_web_only_must_not_enable_or_start_in_preview(setup_files):
    rendered, units, rules, _ = setup_files
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/install_runtime_services.py'),
         '--rendered-dir', str(rendered), '--unit-dir', str(units),
         '--polkit-dir', str(rules), '--web-only', '--start'],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert not units.exists()
