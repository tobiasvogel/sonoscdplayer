"""Installation: config files in /etc/sonoscdplayer, config swap, rendered systemd units and the no-local-paths guard."""
import importlib.util
import json
import os
import re
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]
from tools.render_runtime_units import (
    EARLY_UNIT,
    NETWORK_RULE,
    POLKIT_RULE,
    UNITS,
    existing_dropins,
    render,
    main,
)
from sonoscdplayer.ui.config import DEFAULT_CONFIG, UIConfig, load_ui_config
spec = importlib.util.spec_from_file_location('install_config', ROOT / 'tools/install_config.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
MODULE_PATH = Path(__file__).resolve().parents[2] / 'tools/config_swap.py'
config_swap_spec = importlib.util.spec_from_file_location('config_swap', MODULE_PATH)
cs = importlib.util.module_from_spec(config_swap_spec)
config_swap_spec.loader.exec_module(cs)
OLD = b'[audio_startup]\npcm_timeout_seconds = 45\n\n[wifi]\nsecret = "hunter2"\n'
NEW = b'[audio_startup]\npcm_timeout_seconds = 60\n\n[wifi]\nsecret = "hunter2"\n'
REFERENCE = b'[audio_startup]\npcm_timeout_seconds = 45\n\n[led_outputs.ambient]\ntimeout_ms = 150\n'
IDLE = {'player': {'mode': 'STOPPED'}, 'sonos': {'owned': False, 'group_cleanup_pending': False},
        'stream': {'active': False}}
# (pattern, label, only check files below this top-level directory or None)
FORBIDDEN = (
    (re.compile(r'/home/[A-Za-z0-9_.-]+'), 'home directory path', None),
    (re.compile(r'/Users/[A-Za-z0-9_.-]+'), 'home directory path', None),
    (re.compile(r'^User=(?!@SONOSCDPLAYER_USER@$|root$).+$', re.MULTILINE),
     'hardcoded service account', 'systemd'),
    (re.compile(r'subject\.user === "(?!@SONOSCDPLAYER_USER@")'),
     'hardcoded polkit account', 'systemd'),
)
TEXT_SUFFIXES = {'', '.py', '.toml', '.md', '.service', '.conf', '.rules',
                 '.sh', '.target', '.example', '.c', '.h', '.json', '.lst'}



class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_dry_run_never_creates_destination(self):
        destination = self.root / 'etc'
        outcomes = installer.install_configs(destination)
        self.assertEqual([o for _, o in outcomes], ['would-copy-example'] * 3)
        self.assertFalse(destination.exists())

    def test_examples_copied_byte_exact_with_restrictive_modes(self):
        destination = self.root / 'etc'
        installer.install_configs(destination, dry_run=False)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o750)
        for name in installer.NAMES:
            target = destination / name
            self.assertEqual(target.read_bytes(), installer.example_for(name).read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_migrates_existing_files_and_falls_back_to_examples(self):
        legacy = self.root / 'old'
        legacy.mkdir()
        (legacy / 'player.toml').write_text('[streaming]\nmax_buffer_mib = 8\n')
        destination = self.root / 'etc'
        outcomes = dict(installer.install_configs(destination, from_dir=legacy, dry_run=False))
        self.assertEqual(outcomes['player.toml'], 'copied-migrated')
        self.assertEqual(outcomes['interface.toml'], 'copied-example')
        self.assertEqual((destination / 'player.toml').read_text(),
                         '[streaming]\nmax_buffer_mib = 8\n')
        self.assertTrue((legacy / 'player.toml').exists(), 'source must not be removed')

    def test_invalid_legacy_toml_is_refused_before_writing(self):
        legacy = self.root / 'old'
        legacy.mkdir()
        (legacy / 'interface.toml').write_text('[broken\n')
        destination = self.root / 'etc'
        with self.assertRaises(Exception):
            installer.install_configs(destination, from_dir=legacy, dry_run=False)
        self.assertFalse(destination.exists())

    def test_second_run_never_overwrites(self):
        destination = self.root / 'etc'
        installer.install_configs(destination, dry_run=False)
        (destination / 'player.toml').write_text('custom = 1\n')
        outcomes = installer.install_configs(destination, dry_run=False)
        self.assertEqual((destination / 'player.toml').read_text(), 'custom = 1\n')
        self.assertTrue(all(o == 'kept-existing' for _, o in outcomes))

    def test_existing_symlink_is_not_followed_or_replaced(self):
        destination = self.root / 'etc'
        destination.mkdir()
        other = self.root / 'other'
        other.write_text('sentinel')
        (destination / 'player.toml').symlink_to(other)
        installer.install_configs(destination, dry_run=False)
        self.assertEqual(other.read_text(), 'sentinel')
        self.assertTrue((destination / 'player.toml').is_symlink())

    def test_main_uses_standard_location_override(self):
        destination = self.root / 'std'
        old = os.environ.get('SONOSCDPLAYER_CONFIG_DIR')
        os.environ['SONOSCDPLAYER_CONFIG_DIR'] = str(destination)
        try:
            self.assertEqual(installer.main(['--apply'] if os.geteuid() != 0 else
                                            ['--apply', '--service-user', 'root']), 0)
        finally:
            if old is None:
                os.environ.pop('SONOSCDPLAYER_CONFIG_DIR')
            else:
                os.environ['SONOSCDPLAYER_CONFIG_DIR'] = old
        self.assertEqual(sorted(p.name for p in destination.iterdir()),
                         sorted(installer.NAMES))


def busy(**changes):
    state = json.loads(json.dumps(IDLE))
    for path, value in changes.items():
        section, key = path.split('__')
        state[section][key] = value
    return state


class FakeSystemctl:
    """Records calls; ``fail`` is a set of (action, nth call) pairs that return 1."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, cmd, **_kwargs):
        action = cmd[1]
        self.calls.append(action)
        nth = self.calls.count(action)
        return subprocess.CompletedProcess(cmd, 1 if (action, nth) in self.fail else 0, '', '')


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = self.dir / 'player.toml'
        self.config.write_bytes(OLD)
        os.chmod(self.config, 0o640)
        self.new = self.dir / 'new.toml'
        self.new.write_bytes(NEW)
        self.reference = self.dir / 'reference.toml'
        self.reference.write_bytes(REFERENCE)
        self.lines = []

    def swap(self, *, run=None, state=IDLE, **kwargs):
        run = run or FakeSystemctl()
        kwargs.setdefault('apply', True)
        result = cs.swap(self.config, self.new, services=['player.service'], run=run,
                         read_state=lambda: state, sleep=lambda _s: None,
                         out=self.lines.append, **kwargs)
        return run, result

    def backups(self):
        return sorted(self.dir.glob('player.toml.bak-*'))


class SwapTests(Case):
    def test_dry_run_changes_nothing(self):
        run, result = self.swap(apply=False)
        self.assertEqual(result, 0)
        self.assertEqual(run.calls, [])
        self.assertEqual(self.config.read_bytes(), OLD)
        self.assertEqual(self.backups(), [])
        self.assertIn('dry run', self.lines[-1])

    def test_apply_replaces_file_and_restarts(self):
        run, result = self.swap()
        self.assertEqual(result, 0)
        self.assertEqual(run.calls, ['stop', 'start', 'is-active'])
        self.assertEqual(self.config.read_bytes(), NEW)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        (backup,) = self.backups()
        self.assertEqual(backup.read_bytes(), OLD)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(list(self.dir.glob('.player.toml.*')), [])

    def test_identical_file_is_a_no_op(self):
        self.new.write_bytes(OLD)
        run, result = self.swap()
        self.assertEqual((run.calls, result), ([], 0))
        self.assertEqual(self.backups(), [])

    def test_symlinked_config_is_refused(self):
        real = self.dir / 'real.toml'
        real.write_bytes(OLD)
        self.config.unlink()
        self.config.symlink_to(real)
        with self.assertRaises(cs.SwapError):
            self.swap()


class BusyPlayerTests(Case):
    def refused(self, state, **kwargs):
        with self.assertRaises(cs.SwapError) as ctx:
            self.swap(state=state, **kwargs)
        self.assertEqual(self.config.read_bytes(), OLD)
        self.assertEqual(self.backups(), [])
        return str(ctx.exception)

    def test_refuses_while_playing(self):
        self.assertIn('PLAYING', self.refused(busy(player__mode='PLAYING')))

    def test_refuses_while_paused_starting_or_owned_or_streaming(self):
        for changes in ({'player__mode': 'PAUSED'}, {'player__mode': 'STARTING'},
                        {'sonos__owned': True}, {'sonos__group_cleanup_pending': True},
                        {'stream__active': True}):
            with self.subTest(changes=changes):
                self.refused(busy(**changes))

    def test_refuses_when_status_cannot_be_read(self):
        self.assertIn('unavailable', self.refused({'player': {}}))
        self.assertIn('unavailable', self.refused('garbage'))

    def test_unreachable_status_socket_is_refused(self):
        def broken():
            raise TimeoutError
        with self.assertRaises(cs.SwapError):
            cs.swap(self.config, self.new, services=['p.service'], apply=True,
                    read_state=broken, run=FakeSystemctl(), out=self.lines.append)

    def test_no_running_player_is_safe(self):
        run, _ = self.swap(state=None)
        self.assertEqual(run.calls, ['stop', 'start', 'is-active'])
        self.assertEqual(self.config.read_bytes(), NEW)

    def test_force_swaps_even_while_playing(self):
        run, _ = self.swap(state=busy(player__mode='PLAYING'), force=True)
        self.assertEqual(run.calls[0], 'stop')
        self.assertEqual(self.config.read_bytes(), NEW)


class ValidationTests(Case):
    def test_invalid_toml_is_rejected_before_anything_happens(self):
        self.new.write_bytes(b'[broken\nx = ')
        run = FakeSystemctl()
        with self.assertRaises(cs.SwapError):
            self.swap(run=run)
        self.assertEqual((run.calls, self.config.read_bytes()), ([], OLD))

    def test_type_conflict_with_reference_blocks_the_swap(self):
        self.new.write_bytes(b'[audio_startup]\npcm_timeout_seconds = "sixty"\n')
        run = FakeSystemctl()
        with self.assertRaises(cs.SwapError) as ctx:
            self.swap(run=run, reference=self.reference)
        self.assertIn('audio_startup.pcm_timeout_seconds', str(ctx.exception))
        self.assertNotIn('sixty', str(ctx.exception))
        self.assertEqual(run.calls, [])

    def test_missing_and_extra_keys_are_reported_by_name_only(self):
        self.swap(reference=self.reference)
        text = '\n'.join(self.lines)
        self.assertIn('led_outputs', text)            # whole table missing: defaults apply
        self.assertIn('wifi', text)                   # not in reference
        self.assertNotIn('hunter2', text)             # values are never printed
        self.assertEqual(self.config.read_bytes(), NEW)

    def test_compare_walks_nested_tables(self):
        missing, conflicts, extra = cs.compare(
            {'a': {'x': 1, 'z': 1}, 'b': 'text'}, {'a': {'x': 'str', 'y': 2}, 'b': 'ok', 'c': {'d': 1}})
        self.assertEqual((missing, conflicts, extra), (['a.y', 'c'], ['a.x'], ['a.z']))


class FailureTests(Case):
    def test_failed_stop_leaves_everything_untouched(self):
        run = FakeSystemctl(fail={('stop', 1)})
        with self.assertRaises(cs.SwapError):
            self.swap(run=run)
        self.assertEqual(run.calls, ['stop'])
        self.assertEqual(self.config.read_bytes(), OLD)
        self.assertEqual(self.backups(), [])

    def test_failed_start_restores_previous_configuration(self):
        run = FakeSystemctl(fail={('start', 1)})
        with self.assertRaises(cs.SwapError) as ctx:
            self.swap(run=run)
        self.assertIn('restored', str(ctx.exception))
        self.assertEqual(self.config.read_bytes(), OLD)
        self.assertEqual(run.calls, ['stop', 'start', 'reset-failed', 'start'])

    def test_service_that_does_not_stay_active_is_rolled_back(self):
        run = FakeSystemctl(fail={('is-active', 1)})
        with self.assertRaises(cs.SwapError):
            self.swap(run=run)
        self.assertEqual(self.config.read_bytes(), OLD)

    def test_failed_rollback_names_the_backup(self):
        run = FakeSystemctl(fail={('start', 1), ('start', 2)})
        with self.assertRaises(cs.SwapError) as ctx:
            self.swap(run=run)
        (backup,) = self.backups()
        self.assertIn('rollback failed', str(ctx.exception))
        self.assertIn(backup.name, str(ctx.exception))
        self.assertEqual(backup.read_bytes(), OLD)


class StatusClientTests(unittest.TestCase):
    def serve_once(self, path, reply):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        received = []

        def handle():
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(256))
                conn.sendall(reply)
            server.close()

        thread = threading.Thread(target=handle, daemon=True)
        thread.start()
        return thread, received

    def test_reads_state_from_the_control_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's'
            thread, received = self.serve_once(
                path, (json.dumps({'ok': True, 'state': IDLE}) + '\n').encode())
            self.assertEqual(cs.player_state(path), IDLE)
            thread.join(2)
            self.assertEqual(json.loads(received[0]), {'command': 'status'})

    def test_no_socket_means_no_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cs.player_state(Path(tmp) / 'missing.sock'))

    def test_malformed_reply_is_an_error_not_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's'
            thread, _ = self.serve_once(path, b'not json\n')
            self.assertIsNotNone(cs.busy_reason(lambda: cs.player_state(path)))
            thread.join(2)


class RuntimeUnitRenderTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            checkout='/opt/example/src',
            python_venv='/opt/example/.venv/bin/python',
            user='example',
        )
        env = mock.patch.dict(os.environ, {'SONOSCDPLAYER_CONFIG_DIR': '/etc/example'})
        env.start()
        self.addCleanup(env.stop)

    def test_renders_four_complete_units_and_preserves_safety_controls(self):
        units = render(ROOT / 'systemd', **self.inputs)
        self.assertEqual(set(units), set(UNITS) | {EARLY_UNIT, POLKIT_RULE, NETWORK_RULE})
        for name in UNITS:
            content = units[name]
            self.assertIn('WorkingDirectory=/opt/example/src', content)
            self.assertIn('PartOf=sonoscdplayer.target', content)
            self.assertIn('ExecStart=', content)
            self.assertNotIn('preview only', content.lower())
            self.assertNotIn('sonoscdplayer-inp04', content)
            self.assertIn('User=example\n', content)
            self.assertNotIn('@SONOSCDPLAYER_', content)
            self.assertNotIn('#@', content)
        player = units['sonoscdplayer-player.service']
        self.assertIn('RuntimeDirectory=sonoscdplayer\n', player)
        self.assertIn('RuntimeDirectoryMode=0700', player)
        self.assertIn('UMask=0077', player)
        self.assertIn('Restart=no', player)
        self.assertIn('--config /etc/example/player.toml', player)
        led = units['sonoscdplayer-led.service']
        self.assertIn('ExecStart=/usr/bin/python3 ', led)
        self.assertIn('After=sonoscdplayer-early-power-led.service', led)
        self.assertIn('ExecStartPre=-+/usr/bin/systemctl stop '
                      'sonoscdplayer-early-power-led.service', led)
        oled = units['sonoscdplayer-oled.service']
        self.assertIn('RuntimeDirectory=sonoscdplayer-ui', oled)
        self.assertIn('RuntimeDirectoryMode=0700', oled)
        self.assertIn('--ui-target-rpc', oled)
        self.assertNotIn('--oled-recover-on-error', oled)
        self.assertNotIn('--ambient-auto', oled)
        self.assertIn('--display-config /etc/example/player.toml', oled)
        self.assertIn('--ui-config /etc/example/ui.toml', oled)
        self.assertNotIn('--adopt-boot-splash', oled)
        buttons = units['sonoscdplayer-buttons.service']
        self.assertIn('After=local-fs.target sonoscdplayer-oled.service', buttons)
        self.assertIn('--config /etc/example/interface.toml', buttons)
        self.assertIn('--execute --selected-target-execute', buttons)
        self.assertNotIn('[Install]', buttons)

    def test_optional_switches_are_enabled_by_default(self):
        units = render(ROOT / 'systemd', **self.inputs)
        self.assertIn('--execute --selected-target-execute --power-actions '
                      '--drive-activity-events --connect-gestures',
                      units['sonoscdplayer-buttons.service'])
        oled = units['sonoscdplayer-oled.service']
        self.assertIn('--ui-target-rpc --ui-power-actions --ui-network-setup', oled)
        # The cover mode comes from ui.toml, never fixed in the unit.
        self.assertNotIn('--cover', oled)
        self.assertIn('AmbientCapabilities=CAP_NET_BIND_SERVICE', oled)
        for name in (POLKIT_RULE, NETWORK_RULE):
            rule = units[name]
            self.assertIn('subject.user === "example"', rule)
            self.assertNotIn('@SONOSCDPLAYER_', rule)
        network = units[NETWORK_RULE]
        for action in ('wifi.scan', 'wifi.share.protected',
                       'settings.modify.system', 'network-control'):
            self.assertIn('org.freedesktop.NetworkManager.' + action, network)

    def test_each_optional_switch_can_be_disabled(self):
        units = render(ROOT / 'systemd', power_actions=False,
                       drive_activity_events=False, connect_gestures=False,
                       network_setup=False, **self.inputs)
        buttons = units['sonoscdplayer-buttons.service']
        for flag in ('--power-actions', '--drive-activity-events', '--connect-gestures'):
            self.assertNotIn(flag, buttons)
        oled = units['sonoscdplayer-oled.service']
        self.assertNotIn('--ui-power-actions', oled)
        self.assertNotIn('--ui-network-setup', oled)
        self.assertNotIn('AmbientCapabilities', oled)
        self.assertNotIn(POLKIT_RULE, units)
        self.assertNotIn(NETWORK_RULE, units)

    def test_rejects_root_or_malformed_service_user(self):
        for invalid in ('root', '', 'Pi', 'a b', 'x;rm', '../x'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    render(ROOT / 'systemd', **dict(self.inputs, user=invalid))

    def test_unknown_placeholder_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for name in UNITS:
                (source / name).write_text(
                    (ROOT / 'systemd' / name).read_text(encoding='utf-8'),
                    encoding='utf-8')
            player = source / UNITS[0]
            player.write_text(player.read_text(encoding='utf-8') +
                              'Environment=X=@SONOSCDPLAYER_UNKNOWN@\n',
                              encoding='utf-8')
            with self.assertRaises(ValueError):
                render(source, **self.inputs)

    def test_main_refuses_checkout_without_src_layout(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            for checkout, hint in ((str(ROOT), 'src/'), (directory, None)):
                with self.subTest(checkout=checkout):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                        main(['--output-dir', str(Path(directory) / 'out'),
                              '--checkout', checkout,
                              '--python-venv', self.inputs['python_venv'],
                              '--service-user', self.inputs['user']])
                    self.assertIn('lacks sonoscdplayer/player/service.py', stderr.getvalue())
                    if hint:
                        self.assertIn(hint, stderr.getvalue())
                    self.assertFalse((Path(directory) / 'out').exists())

    def test_no_unit_conflicts_with_the_early_led(self):
        # Conflicts= between units of the same boot transaction makes systemd
        # drop the early LED start job, so the breathing LED never appears.
        for path in (ROOT / 'systemd').glob('sonoscdplayer-*'):
            if path.is_file():
                with self.subTest(unit=path.name):
                    self.assertNotRegex(path.read_text(encoding='utf-8'),
                                        r'(?m)^Conflicts=.*early-power-led')

    def test_early_boot_unit_uses_config_and_preserves_boot_ordering(self):
        units = render(ROOT / 'systemd', **self.inputs)
        early = units[EARLY_UNIT]
        source = (ROOT / 'systemd' / EARLY_UNIT).read_text()
        self.assertIn('--execute --config /etc/example/player.toml', early)
        self.assertEqual(early.count('ExecStart='), 1)
        self.assertEqual(
            [line for line in early.splitlines() if not line.startswith('ExecStart=')],
            [line for line in source.splitlines()
             if not line.startswith(('ExecStart=', '#@'))])
        self.assertIn('Before=basic.target', early)
        self.assertIn('WantedBy=basic.target', early)
        self.assertNotIn('PartOf=', early)

    def test_early_unit_rejects_already_configured_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in UNITS:
                (root / name).write_text((ROOT / 'systemd' / name).read_text())
            source = (ROOT / 'systemd' / EARLY_UNIT).read_text()
            (root / EARLY_UNIT).write_text(
                source.replace('--execute', '--execute --config /already.toml'))
            with self.assertRaises(ValueError):
                render(root, **self.inputs)

    def test_existing_dropins_only_reports_units_with_conf_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sonoscdplayer-led.service.d').mkdir()
            (root / 'sonoscdplayer-led.service.d' / 'override.conf').write_text('[Service]\n')
            (root / 'sonoscdplayer-oled.service.d').mkdir()
            before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
            self.assertEqual(existing_dropins(root, UNITS + (EARLY_UNIT,)),
                             ['sonoscdplayer-led.service'])
            self.assertEqual(sorted(str(path.relative_to(root)) for path in root.rglob('*')),
                             before)

    def test_main_warns_without_changing_installed_dropins(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            systemd = root / 'systemd'
            (systemd / (EARLY_UNIT + '.d')).mkdir(parents=True)
            dropin = systemd / (EARLY_UNIT + '.d') / 'override.conf'
            dropin.write_text('[Service]\n')
            staged = root / 'staged'
            args = ['--repo-root', str(ROOT), '--output-dir', str(staged),
                    '--systemd-dir', str(systemd),
                    '--checkout', str(ROOT / 'src'),
                    '--python-venv', self.inputs['python_venv'],
                    '--service-user', self.inputs['user']]
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                main(args)
            self.assertIn('WARNING', buffer.getvalue())
            self.assertIn(EARLY_UNIT + '.d', buffer.getvalue())
            self.assertEqual(dropin.read_text(), '[Service]\n')

    def test_rejects_relative_or_ambiguous_paths(self):
        for invalid in ('relative/src', '/bad path', '/bad\tpath'):
            with self.subTest(invalid=invalid):
                options = dict(self.inputs, checkout=invalid)
                with self.assertRaises(ValueError):
                    render(ROOT / 'systemd', **options)

    def test_only_writes_requested_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'staged'
            argv = ['--repo-root', str(ROOT), '--output-dir', str(out)]
            for name, value in (
                ('--checkout', str(ROOT / 'src')),
                ('--python-venv', self.inputs['python_venv']),
                ('--service-user', self.inputs['user']),
            ):
                argv.extend((name, value))
            main(argv + ['--no-power-actions', '--no-network-setup'])
            self.assertEqual({p.name for p in out.iterdir()}, set(UNITS) | {EARLY_UNIT})
            main(argv)
            self.assertEqual({p.name for p in out.iterdir()},
                             set(UNITS) | {EARLY_UNIT, POLKIT_RULE, NETWORK_RULE})


def tracked_text_files():
    try:
        names = subprocess.run(['git', 'ls-files'], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout.split()
        paths = [ROOT / name for name in names]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob('*') if '.git' not in p.parts]
    for path in paths:
        if path.is_file() and path.suffix in TEXT_SUFFIXES and \
                path != Path(__file__).resolve():
            yield path


class NoLocalPathsTests(unittest.TestCase):
    def test_repository_contains_no_local_paths_or_accounts(self):
        findings = []
        for path in tracked_text_files():
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            top = path.relative_to(ROOT).parts[0]
            for pattern, label, scope in FORBIDDEN:
                if scope is not None and top != scope:
                    continue
                for match in pattern.finditer(text):
                    line = text.count('\n', 0, match.start()) + 1
                    findings.append(f'{path.relative_to(ROOT)}:{line}: {label}')
        self.assertEqual(findings, [])

    def test_ui_config_defaults_to_standard_location(self):
        self.assertIsNone(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'SONOSCDPLAYER_CONFIG_DIR': directory}):
            self.assertEqual(load_ui_config(), UIConfig())  # absent: built-in defaults
            (Path(directory) / 'ui.toml').write_text('[ui]\nlanguage = "en"\n')
            self.assertEqual(load_ui_config().language, 'en')


if __name__ == '__main__':
    unittest.main()
