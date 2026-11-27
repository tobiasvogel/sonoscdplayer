"""A reconnect to a fully delivered track must not wait for a full reread."""
import http.client
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from sonoscdplayer.audio.track_http_server import (
    CHUNK, DiscServer, ProbeCache, TrackStream, wav_header,
)


SIZES = {2: 12 * CHUNK, 3: 8 * CHUNK}
SESSION = 'a' * 32


def pcm(track, start=0, end=None):
    """Distinct track and absolute frame positions, including unaligned ranges."""
    end = SIZES[track] if end is None else end
    return b''.join(struct.pack('<I', (track << 24) | frame)
                    for frame in range(start // 4, end // 4))


class DiscReconnectTests(unittest.TestCase):
    def test_probe_then_late_range_after_prefetch(self):
        # Mock only optical I/O. The real server, ring, HTTP handlers and
        # producer lifecycle run on loopback with no Sonos or drive access.
        bodies = {track: pcm(track) for track in SIZES}
        prefetch_started = threading.Event()
        replay_started = threading.Event()
        probe_disconnected = threading.Event()
        allow_probe_tail = threading.Event()
        finish_replay = threading.Event()
        starts = []
        starts_lock = threading.Lock()

        def command_for(track):
            # The real server recognizes cdparanoia commands as seekable.
            # _read_once is replaced below; no subprocess can be launched.
            return ['cdparanoia', '-r', str(track), '-']

        def wait_gate(stream, gate):
            while not gate.wait(0.01):
                if stream._cancel.is_set():
                    raise OSError('synthetic reader cancelled')
            if stream._cancel.is_set():
                raise OSError('synthetic reader cancelled')

        def read_once(stream):
            track = int(stream.command[stream.command.index('-r') + 1])
            with starts_lock:
                earlier = any(n == track for n, _ in starts)
                starts.append((track, stream.start_offset))
            slow_replay = track == 2 and earlier and stream.start_offset == 0
            if slow_replay:
                replay_started.set()
            if track == 3:
                prefetch_started.set()
            while stream.written < stream.expected:
                if stream._cancel.is_set():
                    raise OSError('synthetic reader cancelled')
                start = stream.written
                if slow_replay and start == CHUNK:
                    wait_gate(stream, allow_probe_tail)
                if slow_replay and start >= 3 * CHUNK:
                    wait_gate(stream, finish_replay)
                block = bodies[track][start:start + CHUNK]
                if stream.probe_cache is not None:
                    stream.probe_cache.capture(start, block)
                stream.ring.write(block, timeout=stream.write_timeout)
                stream.written += len(block)

        with patch.object(TrackStream, '_read_once', read_once):
            stream = TrackStream(SIZES[2], 24 * CHUNK, command_for(2),
                                 write_timeout=0.5, probe_cache=ProbeCache(CHUNK))
            server = DiscServer(('127.0.0.1', 0), stream, 2, tracks=SIZES,
                                command_for=command_for, session_id=SESSION)
            # Keep the replay alive between the two requests even on slow CI.
            # This models the immediate reconnect inside the real orphan grace.
            server.ORPHAN_GRACE_SECONDS = 20
            # Scale the production distance threshold to this short fixture.
            server.RANGE_RESTART_MIN_AHEAD_BYTES = 4 * CHUNK
            original_release = server.release_get

            def release_get(ended, disconnected, complete=False):
                original_release(ended, disconnected, complete=complete)
                if (disconnected and ended is not stream and
                        ended.expected == SIZES[2]):
                    probe_disconnected.set()

            server.release_get = release_get
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                stream.start()
                path = f'/session/{SESSION}/track/2.wav'
                expected = wav_header(SIZES[2]) + bodies[2]
                status, _, data = self._get(server, path)
                self.assertEqual((status, data), (200, expected))
                self.assertTrue(prefetch_started.wait(5), 'next track was not prefetched')
                # Plain GET, then abort after the first PCM chunk, as observed
                # on Sonos. A TCP reset avoids waiting for a normal FIN.
                with socket.create_connection(('127.0.0.1', server.server_port),
                                              timeout=8) as probe:
                    probe.sendall(f'GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
                    received = bytearray()
                    while b'\r\n\r\n' not in received:
                        block = probe.recv(4096)
                        self.assertTrue(block, 'probe closed before headers')
                        received.extend(block)
                    headers, body = bytes(received).split(b'\r\n\r\n', 1)
                    self.assertIn(b' 200 ', headers.splitlines()[0])
                    while len(body) < 44 + CHUNK:
                        block = probe.recv(44 + CHUNK - len(body))
                        self.assertTrue(block, 'probe closed before first PCM chunk')
                        body += block
                    self.assertEqual(body[:44 + CHUNK], expected[:44 + CHUNK])
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                     struct.pack('ii', 1, 0))
                allow_probe_tail.set()
                if replay_started.is_set():
                    self.assertTrue(probe_disconnected.wait(5),
                                    'probe handler did not release its reader')
                # Request a late, deliberately unaligned WAV byte offset.
                # A cache hit or sector-seek restart can satisfy this while
                # the sequential reread is held before the requested offset.
                offset = 44 + 10 * CHUNK + 1
                try:
                    status, headers, data = self._get(
                        server, path, {'Range': f'bytes={offset}-'})
                except (OSError, http.client.HTTPException) as exc:
                    self.fail(f'late range could not be served without sequential reread: {exc!r}')
                self.assertEqual(status, 206)
                self.assertEqual(headers.get('Content-Range'),
                                 f'bytes {offset}-{len(expected) - 1}/{len(expected)}')
                self.assertEqual(data, expected[offset:])
            finally:
                allow_probe_tail.set()
                finish_replay.set()
                server.shutdown()
                server.server_close()
                thread.join(5)

    @staticmethod
    def _get(server, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            conn.request('GET', path, headers=headers or {})
            with conn.getresponse() as response:
                return response.status, response.headers, response.read()
        finally:
            conn.close()


class DiscReconnectSafetyTests(unittest.TestCase):
    """Guard normal delivery and resource limits while improving reconnects."""

    @staticmethod
    def _path(track, session=SESSION):
        return f'/session/{session}/track/{track}.wav'

    def _start(self, budget=24 * CHUNK, *, hold_prefetch=False,
               fail_first_prefetch=False):
        bodies = {track: pcm(track) for track in SIZES}
        started = threading.Condition()
        starts = []
        active = set()
        peak = [0]
        gate = threading.Event()
        failed = threading.Event()

        def command_for(track):
            return ['cdparanoia', '-r', str(track), '-']

        def read_once(stream):
            track = int(stream.command[stream.command.index('-r') + 1])
            with started:
                first = not any(n == track for n, _ in starts)
                starts.append((track, stream))
                active.add(stream)
                peak[0] = max(peak[0], len(active))
                started.notify_all()
            try:
                if track == 3 and first and hold_prefetch:
                    while not gate.wait(0.01):
                        if stream._cancel.is_set():
                            raise OSError('synthetic reader cancelled')
                if track == 3 and first and fail_first_prefetch:
                    failed.set()
                    raise OSError('synthetic optical read failure')
                while stream.written < stream.expected:
                    if stream._cancel.is_set():
                        raise OSError('synthetic reader cancelled')
                    offset = stream.written
                    block = bodies[track][offset:offset + CHUNK]
                    if stream.probe_cache is not None:
                        stream.probe_cache.capture(offset, block)
                    stream.ring.write(block, timeout=stream.write_timeout)
                    stream.written += len(block)
            finally:
                with started:
                    active.remove(stream)
                    started.notify_all()

        patcher = patch.object(TrackStream, '_read_once', read_once)
        patcher.start()
        self.addCleanup(patcher.stop)
        stream = TrackStream(SIZES[2], budget, command_for(2),
                             write_timeout=3, probe_cache=ProbeCache(CHUNK))
        server = DiscServer(('127.0.0.1', 0), stream, 2, tracks=SIZES,
                            command_for=command_for, session_id=SESSION)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            gate.set()
            server.shutdown()
            try:
                server.server_close()
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive(), 'HTTP server did not stop')
            with started:
                self.assertFalse(active, 'optical producer survived shutdown')
                self.assertEqual(peak[0], 1, 'overlapping optical producers')

        self.addCleanup(cleanup)
        stream.start()
        return server, bodies, started, starts, gate, failed

    def _wait_prefetch(self, started, starts):
        with started:
            self.assertTrue(started.wait_for(
                lambda: any(n == 3 for n, _ in starts), timeout=5),
                'next track was not prefetched')
            return next(stream for n, stream in starts if n == 3)

    def _full_get(self, server, bodies, track):
        status, headers, data = DiscReconnectTests._get(server, self._path(track))
        expected = wav_header(SIZES[track]) + bodies[track]
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Content-Length'), str(len(expected)))
        self.assertEqual(data, expected)

    def _assert_pcm_budget(self, server, budget):
        # Count distinct persistent allocations, including retired generations.
        # Shared ring/cache references are counted once, not once per stream.
        # This is an allocation check for the existing ring/cache architecture,
        # not a bound on Python RSS or transient HTTP response buffers.
        with server._generation_lock:
            allocations = {}
            for stream in server._streams:
                ring = stream.ring
                allocations[id(ring._buf)] = len(ring._buf)
                if stream.probe_cache is not None:
                    data = stream.probe_cache.data
                    allocations[id(data)] = len(data)
            self.assertLessEqual(sum(allocations.values()), budget)

    def test_full_get_of_previous_track_after_prefetch(self):
        server, bodies, started, starts, _, _ = self._start()
        self._full_get(server, bodies, 2)
        self._wait_prefetch(started, starts)
        # A normal full GET is not necessarily a reconnect probe.
        self._full_get(server, bodies, 2)
        self._full_get(server, bodies, 3)

    def test_active_http_cursor_is_not_preempted_by_previous_track(self):
        server, bodies, started, starts, gate, _ = self._start(hold_prefetch=True)
        self._full_get(server, bodies, 2)
        ahead = self._wait_prefetch(started, starts)
        server.SWITCH_WAIT_SECONDS = 0.25
        # Model a client that has already streamed PCM and still owns a cursor.
        # Hold the producer until the competing request has been examined.
        reader = ahead.attach(0)
        ahead.http_begin(reader, 'synthetic-client')
        ahead.http_sent(reader, CHUNK)
        try:
            status, _, data = DiscReconnectTests._get(server, self._path(2))
            if status == 200:
                # A future cache may serve the old track without touching the
                # active producer or its cursor.
                self.assertEqual(data, wav_header(SIZES[2]) + bodies[2])
            else:
                self.assertEqual(status, 409)
            self.assertIn(reader, ahead.ring.reader_ids())
            self.assertFalse(ahead._cancel.is_set())
            self.assertTrue(ahead.thread.is_alive())
        finally:
            ahead.http_end(reader)
            ahead.ring.unregister(reader)
            gate.set()
        self._full_get(server, bodies, 3)

    def test_reconnect_does_not_overlap_optical_producers(self):
        server, bodies, started, starts, _, _ = self._start(hold_prefetch=True)
        self._full_get(server, bodies, 2)
        self._wait_prefetch(started, starts)
        # The previous track may be cached or the in-flight prefetch may be
        # cancelled. Either way, cleanup verifies peak optical concurrency.
        self._full_get(server, bodies, 2)

    def test_small_budget_across_track_switches(self):
        budget = 4 * CHUNK
        server, bodies, started, starts, _, _ = self._start(budget)
        self._assert_pcm_budget(server, budget)
        self._full_get(server, bodies, 2)
        self._wait_prefetch(started, starts)
        self._assert_pcm_budget(server, budget)
        self._full_get(server, bodies, 3)
        self._assert_pcm_budget(server, budget)
        self._full_get(server, bodies, 2)
        self._assert_pcm_budget(server, budget)

    def test_stale_session_does_not_change_prefetch(self):
        server, bodies, started, starts, _, _ = self._start(hold_prefetch=True)
        self._full_get(server, bodies, 2)
        ahead = self._wait_prefetch(started, starts)
        with started:
            before = list(starts)
        for track in (2, 3):
            for headers in ({}, {'Range': 'bytes=45-'}):
                with self.subTest(track=track, headers=headers):
                    status, _, _ = DiscReconnectTests._get(
                        server, self._path(track, 'b' * 32), headers)
                    self.assertEqual(status, 404)
        with started:
            self.assertEqual(starts, before)
        self.assertIs(server.stream, ahead)
        self.assertFalse(ahead._cancel.is_set())

    def test_failed_prefetch_is_not_served_as_complete_audio(self):
        server, bodies, started, starts, _, failed = self._start(
            fail_first_prefetch=True)
        self._full_get(server, bodies, 2)
        ahead = self._wait_prefetch(started, starts)
        self.assertTrue(failed.wait(5))
        ahead.thread.join(5)
        self.assertFalse(ahead.thread.is_alive())
        self.assertIsNotNone(ahead.error)
        self._full_get(server, bodies, 3)
        with started:
            self.assertGreaterEqual(sum(n == 3 for n, _ in starts), 2)


class RangeReconnectAdmissionTests(unittest.TestCase):
    """Exercise admission and producer lifetime without optical hardware."""

    DISTANCE = 4 * CHUNK
    PRODUCED = 3 * CHUNK

    def _start(self, *, stubborn=False, seekable=True, grace=20):
        from sonoscdplayer.audio.track_http_server import SECTOR

        self.sector = SECTOR
        self.bodies = {track: pcm(track) for track in SIZES}
        self.ready = threading.Event()
        self.finish = threading.Event()
        self.starts = []
        self.active = set()
        self.peak = 0
        self.lock = threading.Lock()

        def command_for(track):
            return ['cdparanoia' if seekable else 'synthetic', '-r', str(track), '-']

        def read_once(stream):
            track = int(stream.command[stream.command.index('-r') + 1])
            with self.lock:
                self.starts.append((track, stream.start_offset))
                self.active.add(stream)
                self.peak = max(self.peak, len(self.active))
            try:
                while stream.written < stream.expected:
                    if stream._cancel.is_set():
                        raise OSError('synthetic reader cancelled')
                    if stream.start_offset == 0 and stream.written >= self.PRODUCED:
                        self.ready.set()
                        while not self.finish.wait(0.01):
                            if not stubborn and stream._cancel.is_set():
                                raise OSError('synthetic reader cancelled')
                        if stream._cancel.is_set():
                            raise OSError('synthetic reader cancelled')
                    offset = stream.written
                    block = self.bodies[track][offset:offset + CHUNK]
                    if stream.probe_cache is not None:
                        stream.probe_cache.capture(offset, block)
                    stream.ring.write(block, timeout=stream.write_timeout)
                    stream.written += len(block)
            finally:
                with self.lock:
                    self.active.remove(stream)

        patcher = patch.object(TrackStream, '_read_once', read_once)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Use the final track so completing a replay cannot start another prefetch.
        stream = TrackStream(SIZES[3], 24 * CHUNK, command_for(3),
                             write_timeout=0.5, probe_cache=ProbeCache(CHUNK))
        server = DiscServer(('127.0.0.1', 0), stream, 3, tracks=SIZES,
                            command_for=command_for, session_id=SESSION)
        server.ORPHAN_GRACE_SECONDS = grace
        server.RANGE_RESTART_MIN_AHEAD_BYTES = self.DISTANCE

        def cleanup():
            self.finish.set()
            server.server_close()
            with self.lock:
                self.assertFalse(self.active, 'producer survived cleanup')
                self.assertEqual(self.peak, 1, 'overlapping producers')

        self.addCleanup(cleanup)
        stream.start()
        self.assertTrue(self.ready.wait(5), 'initial producer did not reach gate')
        self.assertEqual(stream.ring.stats['next_offset'], self.PRODUCED)
        return server, stream

    def _release(self, server, *, disconnected=True):
        # Model the handler's lifecycle, including cursor removal before release.
        stream, reader = server.acquire_get(0, server.track)
        stream.http_begin(reader, 'synthetic-client')
        stream.http_sent(reader, CHUNK)
        stream.http_end(reader)
        stream.ring.unregister(reader)
        server.release_get(stream, disconnected, complete=False)

    def _assert_attaches(self, server, original, offset):
        stream, reader = server.acquire_get(offset, server.track)
        try:
            self.assertIs(stream, original)
            self.assertIs(server.stream, original)
            self.assertFalse(original._cancel.is_set())
            self.assertIn(reader, original.ring.reader_ids())
            self.assertEqual(len(self.starts), 1)
        finally:
            stream.ring.unregister(reader)

    def _assert_restart(self, server, original, offset):
        ring = original.ring
        storage = ring._buf
        generation = server._generation
        stream, reader = server.acquire_get(offset, server.track)
        try:
            self.assertIsNot(stream, original)
            self.assertFalse(original.thread.is_alive())
            self.assertIs(server.stream, stream)
            self.assertIs(stream.ring, ring)
            self.assertIs(ring._buf, storage)
            self.assertEqual(server._generation, generation + 1)
            seek = offset // self.sector * self.sector
            self.assertEqual(stream.start_offset, seek)
            chunks = []
            while True:
                block = ring.read(reader, CHUNK, timeout=5)
                if not block:
                    break
                chunks.append(block)
            self.assertEqual(b''.join(chunks), self.bodies[3][offset:])
            self.assertEqual(self.starts, [(3, 0), (3, seek)])
        finally:
            stream.ring.unregister(reader)

    def test_far_reconnect_restarts_at_sector_boundary(self):
        server, stream = self._start()
        self._release(server)
        self._assert_restart(server, stream, self.PRODUCED + self.DISTANCE + 4)

    def test_threshold_is_inclusive(self):
        server, stream = self._start()
        self._release(server)
        self._assert_restart(server, stream, self.PRODUCED + self.DISTANCE)

    def test_available_range_keeps_producer(self):
        server, stream = self._start()
        self._release(server)
        self._assert_attaches(server, stream, CHUNK)

    def test_range_just_below_threshold_keeps_producer(self):
        server, stream = self._start()
        self._release(server)
        self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE - 4)

    def test_far_range_without_disconnect_keeps_producer(self):
        server, stream = self._start()
        self._release(server, disconnected=False)
        self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE)

    def test_far_range_after_reconnect_window_keeps_producer(self):
        server, stream = self._start(grace=0.05)
        expired = threading.Event()
        # Keep the producer alive after the timer fires to isolate the age check
        # from the existing orphan cancellation/replay path.
        with patch.object(server, '_expire_orphan', lambda _: expired.set()):
            self._release(server)
            self.assertTrue(expired.wait(5), 'orphan timer did not fire')
            self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE)

    def test_other_cursor_prevents_restart(self):
        server, stream = self._start()
        self._release(server)
        # Install a pinned cursor directly so the guard is tested even if a
        # normal admission would already consume the reconnect candidate.
        held = stream.ring.register(0)
        try:
            self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE)
            self.assertIn(held, stream.ring.reader_ids())
        finally:
            stream.ring.unregister(held)

    def test_successful_admission_consumes_reconnect_candidate(self):
        server, stream = self._start()
        self._release(server)
        self._assert_attaches(server, stream, CHUNK)
        self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE)

    def test_nonseekable_producer_is_not_restarted(self):
        server, stream = self._start(seekable=False)
        self._release(server)
        self._assert_attaches(server, stream, self.PRODUCED + self.DISTANCE)

    def test_failed_shutdown_does_not_reset_ring_or_start_producer(self):
        from sonoscdplayer.audio.track_http_server import ReplayBusy

        server, stream = self._start(stubborn=True)
        self._release(server)
        ring = stream.ring
        state = ring.stats
        generation = server._generation
        # Simulate expiry of the bounded join without sleeping for its timeout.
        with patch.object(stream.thread, 'join', return_value=None):
            with self.assertRaises(ReplayBusy):
                server.acquire_get(self.PRODUCED + self.DISTANCE, server.track)
        self.assertIs(server.stream, stream)
        self.assertEqual(server._generation, generation)
        self.assertIs(stream.ring, ring)
        self.assertEqual(ring.stats, state)
        self.assertTrue(stream.thread.is_alive())
        self.assertEqual(self.starts, [(3, 0)])


    def test_track_switch_does_not_carry_reconnect_candidate(self):
        server, original = self._start()
        self._release(server)
        self.ready.clear()
        replacement, reader = server.acquire_get(0, 2)
        self.assertIsNot(replacement, original)
        replacement.ring.unregister(reader)
        server.release_get(replacement, False, complete=False)
        self.assertTrue(self.ready.wait(5), 'replacement did not reach gate')
        self.assertFalse(original.thread.is_alive())
        stream, reader = server.acquire_get(self.PRODUCED + self.DISTANCE, 2)
        try:
            self.assertIs(stream, replacement)
            self.assertFalse(replacement._cancel.is_set())
            self.assertEqual(self.starts, [(3, 0), (2, 0)])
        finally:
            stream.ring.unregister(reader)
