"""Offline tests for tools/config_swap.py: stop, swap the file, start."""
import importlib.util
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools/config_swap.py'
spec = importlib.util.spec_from_file_location('config_swap', MODULE_PATH)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

OLD = b'[audio_startup]\npcm_timeout_seconds = 45\n\n[wifi]\nsecret = "hunter2"\n'
NEW = b'[audio_startup]\npcm_timeout_seconds = 60\n\n[wifi]\nsecret = "hunter2"\n'
REFERENCE = b'[audio_startup]\npcm_timeout_seconds = 45\n\n[led_outputs.ambient]\ntimeout_ms = 150\n'
IDLE = {'player': {'mode': 'STOPPED'}, 'sonos': {'owned': False, 'group_cleanup_pending': False},
        'stream': {'active': False}}


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


if __name__ == '__main__':
    unittest.main()
