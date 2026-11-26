"""The player's playback logic: track server process, Sonos playback, groups.

``Controller`` holds the PlayerState and carries out PLAY, PAUSE, STOP,
NEXT/PREVIOUS and track selection. It starts the track server process
(``TrackServerProcess``), tells Sonos what to play, forms and releases temporary
groups, and follows what Sonos reports (``refresh``). The player service
(player/service.py) owns one Controller and calls it from its worker.

Safety rules: a Sonos STOP is only sent after the speaker was checked twice
to still play our own stream; leaving the service never stops Sonos on its
own; PCM is never written to disk.
"""
from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import re
import time

from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    Timeout as RequestsTimeout,
)

from sonoscdplayer.audio.track_server_process import (forward_server_output, track_stream_url,
                                                      wait_for_reader_start)
from sonoscdplayer.player.state import Mode
from sonoscdplayer.system.version import info as software_info
from sonoscdplayer.sonos.speaker_checks import (SpeakerGrouped, TransportChanged, UriChanged, find_solo_stopped_speaker,
                                                require_our_stream)
from sonoscdplayer.ui.menu.playback_progress import verified_progress
from sonoscdplayer.sonos import groups
from sonoscdplayer.metadata.status_summary import status_metadata
from sonoscdplayer.sonos.track_metadata import (DEFAULT_ALBUM_PREFIX, cover_route, didl_item, playing_from_queue,
                            queue_allowed, queue_item_is_ours, remove_queue_block, track_didl)


class TrackServerProcess:
    """The track HTTP server as child process, started per playback.

    ``base`` is the working directory (src/), ``config`` the player.toml path.
    """
    def __init__(self, base, device, config, port, *, popen=subprocess.Popen, startup_prebuffer=True, startup_timeout=45.0, max_buffer_mib=None):
        self.base, self.device, self.config, self.port = base, device, config, port
        if max_buffer_mib is not None and (type(max_buffer_mib) is not int or not 16 <= max_buffer_mib <= 512):
            raise ValueError('invalid playback buffer size')
        self.max_buffer_mib = max_buffer_mib
        self.popen = popen
        self.startup_prebuffer = startup_prebuffer
        self.startup_timeout = startup_timeout
        self.proc = None
        self._reader_active = False
        self._reader_lock = threading.Lock()

    def reader_active(self):
        """True while the server's CD reader is running."""
        with self._reader_lock:
            return self._reader_active if self.alive() else False

    def _observe_line(self, line):
        with self._reader_lock:
            if 'READER started' in line:
                self._reader_active = True
            elif 'READER end written=' in line:
                self._reader_active = False

    def start(self, track, cover=None, disc=False):
        """Start the server for ``track`` and wait until its reader runs.

        With ``disc=True`` it serves all tracks of the CD (DiscServer).
        """
        if self.proc is not None:
            raise RuntimeError('server already attached')
        events = queue.Queue()
        p = self.popen([sys.executable, '-u', '-m', 'sonoscdplayer.audio.track_http_server',
                        '--device', self.device, '--track', str(track),
                        '--port', str(self.port), '--config', str(self.config)]
                       + (['--max-buffer-mib', str(self.max_buffer_mib)] if self.max_buffer_mib is not None else [])
                       + (['--cover', str(cover)] if cover else [])
                       + (['--disc'] if disc else []),
                       cwd=self.base, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.proc = p
        with self._reader_lock:
            self._reader_active = True  # conservative until reader reports completion
        startup_done = threading.Event()
        threading.Thread(target=forward_server_output,
                         args=(p, events, time.monotonic(), self._observe_line, startup_done),
                         daemon=True).start()
        try:
            wait_for_reader_start(events, p, timeout=30)
            if self.startup_prebuffer:
                wait_startup_pcm(events, p, timeout=self.startup_timeout)
            if p.poll() is not None:
                raise RuntimeError('stream server exited at startup')
        except BaseException:
            self.close()
            raise
        finally:
            startup_done.set()          # nobody reads the event queue after the start

    def alive(self):
        """True while the server process runs."""
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        """Terminate the server process."""
        p, self.proc = self.proc, None
        with self._reader_lock:
            self._reader_active = False
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def wait_startup_pcm(events, proc, *, timeout):
    """Require real PCM before Sonos play_uri; never mistake process start for data."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError('stream server exited before PCM became ready')
        try:
            _, line = events.get(timeout=min(0.2, max(0.01, end - time.monotonic())))
        except queue.Empty:
            continue
        if line is None or 'READER end written=' in line:
            raise RuntimeError('CD reader ended before startup PCM became ready')
        if 'STARTUP_PCM_READY bytes=' in line:
            return
    raise TimeoutError('No CD PCM before startup timeout; Sonos PLAY not sent')


def routed_source_ip(target_ip):
    """Local outbound IPv4 source chosen by kernel for the target speaker."""
    if not target_ip:
        raise RuntimeError('target has no IP address')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((target_ip, 1400))  # UDP connect does NOT send a packet
        result = sock.getsockname()[0]
    if not result or result.startswith('127.') or result == '0.0.0.0':
        raise RuntimeError('no LAN source address for target')
    return result



# A track counts as played out when its last verified position was this close
# to its end (progress is polled every 2 s; Sonos stops shortly after the end).
END_TOLERANCE_SECONDS = 6

# A start that never reaches PLAYING is given up after this long, whatever
# Sonos reports (e.g. stuck in TRANSITIONING). The error is then released,
# so PLAY or STOP on the device can clear it.
STARTUP_LIMIT_SECONDS = 30

# A short Sonos read outage must not immediately cut off a healthy local stream.
# Beyond this limit we release local resources, without sending a Sonos STOP.
SONOS_READ_GRACE_SECONDS = 10.0


class Controller:
    """Playback on Sonos for one CD; see the module docstring.

    Args:
        state: the PlayerState to start with.
        uid: default speaker UID (the service passes the selected one to play()).
        host, port: address under which Sonos reaches the track server.
        server: a TrackServerProcess.
        verify: finds and checks the speaker before PLAY.
        route: optional function returning the LAN address for a speaker.
        find_many: finds group members by UID.
    """
    def __init__(self, state, uid, host, port, server, verify=find_solo_stopped_speaker, route=None, find_many=None):
        self.state, self.uid = state, uid
        self.host, self.port = host, port
        self.server, self.verify = server, verify
        self.route = route  # None retains legacy explicit host behavior
        self.owned_url = None
        self.player = None
        self.last_transport = 'UNKNOWN'
        self.seen_playing = False
        self.start_monotonic = None
        self._sonos_read_failure_since = None
        self._display_progress = None  # read-only Sonos timing; never used to control PLAY
        self._display_progress_poll_at = None
        self.display_toc = None  # validated startup/idle TOC; never read disc for display
        self.metadata_snapshot = None  # asynchronously loaded, TOC-qualified display info
        self.album_prefix = DEFAULT_ALBUM_PREFIX  # [sonos] album_prefix, before the album name
        self.use_queue = True        # [sonos] use_queue: play from the queue to show artist/album
        # Our disc block in the Sonos queue: {'first': 1-based position,
        # 'tracks': [track numbers], 'urls': [url per track]} or None.
        self._queue_block = None
        self.auto_advance = True     # [playback] continue: next track after a track ends
        self._pending_advance = None
        self.stopped_since = None
        self.status_hook = None  # optional read-only snapshot publisher
        self.disc_status = None  # optional independent disc observation (DiscMonitor)
        self.find_many = find_many or groups.find_speakers
        self._joined = {}
        self._group_coord = None
        self._group_members = frozenset()
        self._group_uncertain = False
        self._recovery_url = None
        self.group_store = None      # optional PlaybackGroupStore (survives restarts)
        self.last_rejection = None   # {'seq', 'command', 'reason'} of a refused PLAY
        self.selection_hook = None   # selection_hook(uid, members) when the app changes the group
        self.target_rev = 0          # counts selection changes, for the display

    def note_rejection(self, command, reason):
        """Remember a refused PLAY for the display (status ``last_rejection``)."""
        seq = (self.last_rejection or {}).get('seq', 0) + 1
        text = re.sub(r'RINCON_[0-9A-Za-z]+', '<speaker>', str(reason))[:160]
        self.last_rejection = {'seq': seq, 'command': command, 'reason': text}

    def _store_group(self):
        """Write the current playback group to the group store, if any."""
        if self.group_store is None:
            return
        uids = (({self._group_coord.uid} | set(self._joined))
                if self._joined and self._group_coord is not None else set())
        try:
            self.group_store.save(uids)
        except Exception as exc:
            print(f'[player] playback group not stored: {type(exc).__name__}', flush=True)

    def remembered_group(self):
        """Speakers of the current or last unreleased playback group."""
        current = ({self._group_coord.uid} | set(self._joined)) if self._group_coord else set()
        stored = self.group_store.load() if self.group_store is not None else frozenset()
        return frozenset(current) | stored

    def _url(self, track):
        return track_stream_url(self.host, self.port, track)

    def status(self):
        """One-line summary for the log and the status reply."""
        return (f'mode={self.state.mode.value} track={self.state.selected_track}/'
                f'{len(self.state.track_numbers)} sonos={self.last_transport} '
                f'owned={bool(self.owned_url)} server={self.server.alive()}')
    
    def status_snapshot(self):
        """Read cached state only: no CD, network, or Sonos status queries.

        ``owned`` reports a cached session association, not authorization for
        later destructive commands. Those must repeat live ownership checks.
        """
        active = self.server.alive()
        owned = bool(self.owned_url)
        snapshot = {
            'schema_version': 1,
            'player': {
                'mode': self.state.mode.value,
                'track': self.state.selected_track,
                'track_count': len(self.state.track_numbers),
                'error': self.state.error,
            },
            'sonos': {
                'target_uid': self.uid if owned else None,
                'transport': self.last_transport,
                'owned': owned,
                'group_cleanup_pending': bool(self._joined or self._group_uncertain) and self._group_uncertain,
            },
            'stream': {
                'active': active,
            },
            # Which code runs on the device; read once per process.
            'software': software_info(),
        }
        if (self._display_progress is not None and owned and active
                and self.state.mode in (Mode.PLAYING, Mode.PAUSED)):
            snapshot['progress'] = dict(self._display_progress)
        if self.disc_status is not None:
            snapshot['disc'] = self.disc_status.snapshot()
        snapshot['target_rev'] = self.target_rev
        if self.last_rejection is not None:
            snapshot['last_rejection'] = dict(self.last_rejection)
        if self.metadata_snapshot is not None:
            # Compact on purpose: status readers have small reply limits.
            snapshot['metadata'] = status_metadata(self.metadata_snapshot,
                                                   self.state.selected_track)
        return snapshot

    def _ownership(self, player, expected):
        self._follow_sonos(player, expected=expected)

    def _release_group(self, *, owned_url=None):
        if not self._joined:
            # Nothing (left) to release: also clear a pending cleanup, e.g.
            # when the remaining members left the group on their own.
            if self._group_uncertain or self._group_coord is not None:
                self._group_uncertain = False
                self._group_coord = None
                self._group_members = frozenset()
                self._recovery_url = None
                self._store_group()
            return True
        result = groups.release(self._group_coord, self._joined, owned_url=owned_url)
        _log_release(result)
        remaining = groups.unresolved_members(self._joined, result)
        self._joined = remaining
        self._group_uncertain = bool(remaining)
        if remaining and owned_url is not None:
            self._recovery_url = owned_url
        if not remaining:
            self._group_coord = None
            self._group_members = frozenset()
            self._recovery_url = None
        self._store_group()
        return not remaining

    def resolve_idle_group(self):
        """Release a pending group of our own members if it plays nothing.

        After STOP the release can fail (for example when the coordinator's
        URI no longer proves our stream). The members are still the ones the
        player joined, and if the coordinator is idle nothing is
        interrupted, so they are released without the stream proof. A group
        with other speakers, or one that is playing, stays pending.

        Returns:
            True if no group is pending afterwards.
        """
        if not self._joined:
            return True
        if self.state.mode not in (Mode.STOPPED, Mode.ERROR) or self.owned_url:
            return False
        coordinator = self._group_coord
        try:
            groups.refresh_topology(coordinator)
            if not groups.is_expected_group(coordinator, coordinator.uid, self._joined):
                observed = groups.inspect_recovery(coordinator, self._joined)
                for uid in observed['standalone']:
                    self._joined.pop(uid, None)      # already left on their own
                if not self._joined:
                    self._release_group()
                    return True
                if not groups.is_expected_group(coordinator, coordinator.uid, self._joined):
                    print('[sonos] pending group not released: unexpected topology', flush=True)
                    return False
            transport = coordinator.get_current_transport_info()['current_transport_state']
        except Exception as exc:
            print(f'[sonos] pending group not checked: {type(exc).__name__}', flush=True)
            return False
        if transport not in groups.IDLE_TRANSPORTS:
            print(f'[sonos] pending group not released: coordinator is {transport}', flush=True)
            return False
        return self._release_group()

    def _verify_releasing_leftover(self, uid, members):
        """verify(uid); a grouped coordinator is made alone first.

        A leftover group of our own speakers is split; otherwise the chosen
        speaker alone leaves an idle group (see groups.make_alone()). Then
        the check is repeated.
        """
        try:
            return self.verify(uid)
        except SpeakerGrouped as exc:
            if not groups.make_alone(exc.speaker, self.remembered_group()):
                raise
            self.forget_leftover_group()
        return self.verify(uid)

    def forget_leftover_group(self):
        """A leftover group was split: forget it unless a group is still ours."""
        if not self._joined:
            self._store_group()

    def inspect_group_recovery(self):
        """Read-only status; no Sonos mutations or hidden retry."""
        if not self._joined:
            return {'state': 'clear', 'remaining': 0, 'can_resolve': False}
        result = groups.inspect_recovery(self._group_coord, self._joined)
        return {'state': result['state'], 'remaining': len(self._joined),
                'standalone': len(result['standalone']),
                'attached': len(result['attached']),
                'blocked': len(result['blocked']),
                'can_resolve': (result['state'] == 'attached'
                                and bool(self._recovery_url)
                                and self.state.mode in (Mode.STOPPED, Mode.ERROR)
                                and not self.owned_url)}

    def resolve_group_recovery(self):
        """Explicit recovery only; never adopt an unknown or foreign session."""
        if self.state.mode not in (Mode.STOPPED, Mode.ERROR) or self.owned_url:
            raise RuntimeError('recovery requires inactive player')
        if not self._joined:
            return self.inspect_group_recovery()
        observed = groups.inspect_recovery(self._group_coord, self._joined)
        if observed['state'] == 'blocked':
            raise RuntimeError('recovery blocked by unknown topology')
        for uid in observed['standalone']:
            self._joined.pop(uid)
        if observed['state'] == 'clear':
            self._joined.clear()
            self._group_uncertain = False
            self._group_coord = None
            self._group_members = frozenset()
            self._recovery_url = None
            self._store_group()
            return self.inspect_group_recovery()
        if not self._recovery_url:
            raise RuntimeError('recovery has no verified owned stream')
        # Recheck the coordinator URI before issuing any group mutation.
        if self._group_coord.get_current_track_info().get('uri') != self._recovery_url:
            raise RuntimeError('recovery stream ownership changed')
        result = groups.release(self._group_coord, self._joined,
                                     owned_url=self._recovery_url)
        self._joined = groups.unresolved_members(self._joined, result)
        self._group_uncertain = bool(self._joined)
        if not self._joined:
            self._group_coord = None
            self._group_members = frozenset()
            self._recovery_url = None
        self._store_group()
        return self.inspect_group_recovery()

    def _verify_retained_group(self, uid):
        coordinator = self._group_coord
        if coordinator is None or coordinator.uid != uid:
            raise RuntimeError('retained coordinator mismatch')
        groups.refresh_topology(coordinator)
        if not groups.is_expected_group(coordinator, uid, self._group_members):
            raise RuntimeError('retained group topology changed')
        if coordinator.get_current_transport_info()['current_transport_state'] != 'STOPPED':
            raise RuntimeError('retained coordinator is not STOPPED')
        return coordinator

    def refresh(self):
        """Follow Sonos: confirm playback, notice pause/stop/track changes and errors.

        Called regularly by the service's worker while something plays.
        """
        if self.state.mode not in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            return
        if not self.server.alive():
            self._fail('stream server exited while playing')
            return
        try:
            player = self._follow_group(self.verify_existing())
            transport = player.get_current_transport_info()['current_transport_state']
            self.last_transport = transport
            if (self.state.mode == Mode.STARTING and not self.seen_playing
                    and transport != 'PLAYING' and self.start_monotonic is not None
                    and time.monotonic() - self.start_monotonic >= STARTUP_LIMIT_SECONDS):
                self._fail(f'Sonos did not start playing within {STARTUP_LIMIT_SECONDS} s '
                           f'(transport {transport}); startup aborted')
                return
            if transport == 'TRANSITIONING':
                self._follow_sonos(player)
            if transport == 'PLAYING':
                self.stopped_since = None
                self._ownership(player, 'PLAYING')
                self.seen_playing = True
                if self.state.mode == Mode.STARTING:
                    self.state = self.state.playback_confirmed()
                elif self.state.mode == Mode.PAUSED:
                    self.state = self.state.resumed()
                    print('[player] PLAYING confirmed', flush=True)
            elif transport == 'PAUSED_PLAYBACK' and self.state.mode in (Mode.PLAYING, Mode.PAUSED):
                self._ownership(player, 'PAUSED_PLAYBACK')
                if self.state.mode == Mode.PLAYING:
                    self.state = self.state.paused()
            elif transport == 'STOPPED' and self.state.mode == Mode.STARTING and not self.seen_playing:
                # Sonos can abort a slow first GET without ever reaching PLAYING.
                # Require a grace period so a transient STOPPED does not abort startup.
                now = time.monotonic()
                if self.stopped_since is None:
                    self.stopped_since = now
                if (self.start_monotonic is not None and now - self.start_monotonic >= 20
                        and now - self.stopped_since >= 5):
                    self._fail('Sonos remained STOPPED without confirmed PLAYING; startup aborted')
            elif transport == 'STOPPED' and self.seen_playing:
                # Do not call this a natural end if the URI has changed.
                info = player.get_current_track_info()
                group_proof_url = self.owned_url
                if info.get('uri') != self.owned_url:
                    # At the end of the queue Sonos jumps back to its first
                    # entry - possibly the first track of our own block.
                    # Still a natural end if our block is untouched in the
                    # queue that is playing; otherwise fail closed.
                    if not self._block_intact(player):
                        self._fail('Sonos URI changed before STOPPED')
                        return
                    group_proof_url = None   # proven through our queue block
                    first = self._block_track(info.get('uri'))
                    if first is not None:
                        self.state = self.state.followed(first)
                # In block mode Sonos plays on by itself.
                advance = (None if self._queue_block
                           else self._next_after_end(self._display_progress))
                self.state = self.state.natural_end()
                self.owned_url = None
                self.player = None
                self.server.close()
                if advance is None:
                    # Release the group while our stream still proves it is
                    # ours; emptying the queue first would change the URI.
                    self._release_group(owned_url=group_proof_url)
                else:
                    # Like a CD player: the next track follows. The group stays.
                    self._pending_advance = advance
                self._drop_queue_entry(player)
                print('[player] STOPPED observed; local server closed', flush=True)
            elif transport == 'TRANSITIONING':
                self.stopped_since = None
            elif transport not in ('STOPPED', 'TRANSITIONING', 'PLAYING'):
                self._fail(f'unexpected Sonos transport state: {transport}')
            # Timing is advisory display data, not an ownership or control
            # condition. Isolate Sonos time-query errors from playback health.
            if (self.state.mode in (Mode.PLAYING, Mode.PAUSED)
                    and transport in ('PLAYING', 'PAUSED_PLAYBACK')
                    and self.owned_url):
                now = time.monotonic()
                if (self._display_progress_poll_at is None
                        or now - self._display_progress_poll_at >= 2.0):
                    self._display_progress_poll_at = now
                    self._display_progress = None
                    try:
                        duration = None
                        if self.display_toc is not None and self.state.selected_track is not None:
                            track = self.display_toc.get_track(self.state.selected_track)
                            duration = int(track.duration_seconds + 0.5)
                        self._display_progress = verified_progress(
                            player.get_current_track_info(), self.owned_url,
                            duration_fallback=duration)
                    except Exception:
                        pass
            else:
                self._display_progress = None
                self._display_progress_poll_at = None
            # A successful complete observation restores trust in the live state.
            if self._sonos_read_failure_since is not None:
                print('[player] Sonos status reachable again; ownership rechecked', flush=True)
                self._sonos_read_failure_since = None
        except UriChanged as exc:
            if self._is_our_uri(exc.uri):
                # Sonos went on to the next track of our disc between two
                # reads (it happens right at a track change). The next
                # refresh follows it; nothing foreign is adopted.
                print('[player] Sonos moved to another track of the disc during the check; '
                      'rechecking', flush=True)
            else:
                self._fail(f'ownership/status check failed: RuntimeError: {exc}')
        except TransportChanged as exc:
            # Play/pause in the Sonos app between our two reads: the next
            # refresh sees the new state. UID, group and URI are still checked
            # on every refresh, so nothing foreign is adopted here.
            print(f'[player] transport changed during check ({exc}); rechecking', flush=True)
        except (TimeoutError, ConnectionError,
                RequestsTimeout, RequestsConnectionError) as exc:
            now = time.monotonic()
            if (self.state.mode == Mode.STARTING and not self.seen_playing
                    and self.start_monotonic is not None
                    and now - self.start_monotonic >= STARTUP_LIMIT_SECONDS):
                self._fail(f'Sonos did not start playing within {STARTUP_LIMIT_SECONDS} s '
                           '(status unavailable); startup aborted')
                return
            if self._sonos_read_failure_since is None:
                self._sonos_read_failure_since = now
                print(f'[player] Sonos status temporarily unavailable: {type(exc).__name__}; '
                      'keeping local stream, no Sonos command sent', flush=True)
            if now - self._sonos_read_failure_since < SONOS_READ_GRACE_SECONDS:
                return
            self._fail(f'Sonos status unavailable for {SONOS_READ_GRACE_SECONDS:g} s; '
                       'local stream closed; no Sonos STOP sent')
            return
        except Exception as exc:
            self._fail(f'ownership/status check failed: {type(exc).__name__}: {exc}')
        if self._pending_advance is not None:
            track, self._pending_advance = self._pending_advance, None
            self._advance_to(track)

    def _is_our_uri(self, uri):
        """True for the stream URL we started or one of our disc block."""
        if not uri:
            return False
        block = self._queue_block
        return uri == self.owned_url or bool(block and uri in block['urls'])

    def _follow_group(self, player):
        """Go along with group changes made in the Sonos app.

        Speakers added to or removed from our group become part of the
        playback (and are released at its end like the ones we joined).
        If the playback was moved to another coordinator, which happens
        when the app takes our coordinator out of the group, we follow the
        speaker that now plays our stream. Anything else is left to the
        ownership checks. Returns the coordinator to use.
        """
        if player.uid != self.uid:
            return player                      # another device: the checks refuse it
        group = player.group
        members = {p.uid: p for p in groups.visible_members(group) if p.uid != self.uid}
        if group.coordinator.uid == self.uid and set(members) == set(self._joined):
            return player                      # nothing changed (no extra Sonos calls)
        # Only our coordinator or a speaker of our group may take over.
        candidates = {}
        read_failure = None
        if group.coordinator.uid == self.uid:
            candidates[self.uid] = player
        for member in self._joined.values():
            try:
                coordinator = member.group.coordinator
            except (TimeoutError, ConnectionError,
                    RequestsTimeout, RequestsConnectionError) as exc:
                read_failure = exc
                continue
            except Exception:
                continue
            if coordinator.uid in self._joined:
                candidates.setdefault(coordinator.uid, coordinator)
        # Prefer our own coordinator, then anyone else playing our stream.
        ordered = sorted(candidates.values(), key=lambda c: c.uid != self.uid)
        for coordinator in ordered:
            try:
                transport = coordinator.get_current_transport_info()['current_transport_state']
                uri = coordinator.get_current_track_info().get('uri')
            except (TimeoutError, ConnectionError,
                    RequestsTimeout, RequestsConnectionError) as exc:
                read_failure = exc
                continue
            except Exception:
                continue
            if transport in ('PLAYING', 'PAUSED_PLAYBACK', 'TRANSITIONING') and self._is_our_uri(uri):
                self._adopt_group(coordinator)
                return coordinator
        # No candidate proved ownership; do not infer an end or adopt a
        # different topology from incomplete observations.
        if read_failure is not None:
            raise read_failure
        if group.coordinator.uid == self.uid:
            # Our coordinator, but not playing (yet): only take over membership.
            self._adopt_group(player)
        return player

    def _adopt_group(self, coordinator):
        group = coordinator.group
        members = {p.uid: p for p in groups.visible_members(group) if p.uid != coordinator.uid}
        changed = coordinator.uid != self.uid or set(members) != set(self._joined)
        if coordinator.uid != self.uid:
            print(f'[player] playback moved to another speaker in the Sonos app; '
                  f'following ({len(members)} member(s))', flush=True)
            self.uid = coordinator.uid
            self.player = coordinator
        elif set(members) != set(self._joined):
            print(f'[player] group changed in the Sonos app; now {len(members)} member(s)',
                  flush=True)
        self._joined = members
        self._group_coord = coordinator if members else None
        self._group_members = frozenset(members)
        self._store_group()
        if changed and self.selection_hook is not None:
            # The selection follows changes made in the Sonos app.
            try:
                self.selection_hook(coordinator.uid, tuple(sorted(members)))
            except Exception as exc:
                print(f'[player] selection not updated: {type(exc).__name__}', flush=True)

    def _toc_duration(self, track):
        try:
            return int(self.display_toc.get_track(track).duration_seconds + 0.5)
        except Exception:
            return None

    def _next_after_end(self, progress):
        """Next track number after a real end of track, else None.

        Sonos reports STOPPED the same way for its own STOP button; only a
        last verified position within END_TOLERANCE of the end counts as the
        track having played out. The last track ends the disc as before.
        """
        if not self.auto_advance or not isinstance(progress, dict):
            return None
        elapsed, duration = progress.get('elapsed_seconds'), progress.get('duration_seconds')
        if type(elapsed) is not int or type(duration) is not int or \
                duration - elapsed > END_TOLERANCE_SECONDS:
            return None
        numbers = self.state.track_numbers
        index = numbers.index(self.state.selected_track)
        return numbers[index + 1] if index + 1 < len(numbers) else None

    def _advance_to(self, track):
        members = self._group_members
        try:
            self.state = self.state.select(track)
            print(f'[player] track ended; continuing with track {track}', flush=True)
            self.play(members=members)
        except BaseException as exc:
            # play() already set ERROR; keep a joined group for inspection.
            if self._joined:
                self._group_uncertain = True
                self._recovery_url = None
            print(f'[player] continuing failed: {type(exc).__name__}: {exc}', flush=True)

    def _enqueue_disc(self, player, host, meta, cover_url):
        """Append every track as one block at the END of the queue, play the
        selected one. The user's own queue entries are never touched."""
        tracks = list(self.state.track_numbers)
        urls = [track_stream_url(host, self.port, n) for n in tracks]
        first = None
        for index, (number, url) in enumerate(zip(tracks, urls)):
            didl = track_didl(url, meta or {}, number, album_prefix=self.album_prefix,
                              cover_url=cover_url, duration_seconds=self._toc_duration(number))
            position = player.add_to_queue(didl_item(didl))
            if first is None:
                first = position
                self._queue_block = {'first': first, 'tracks': tracks, 'urls': []}
            elif position != first + index:
                raise RuntimeError('queue changed while adding the disc')
            self._queue_block['urls'].append(url)
        player.play_from_queue(first - 1 + tracks.index(self.state.selected_track))

    def _block_track(self, uri):
        """Track number of ``uri`` if it is one of our block's URLs, else None."""
        block = self._queue_block
        if not block or uri not in block['urls']:
            return None
        return block['tracks'][block['urls'].index(uri)]

    def _follow_sonos(self, player, *, expected=None):
        """Follow our queue track, verifying ownership first when requested.

        Only URLs of our own disc block can become the expected URI. Keep the
        previous track state if a required ownership check fails. Observations
        during TRANSITIONING retain their existing advisory behaviour.
        """
        uri = self.owned_url
        track = None
        if self._queue_block:
            candidate = player.get_current_track_info().get('uri')
            track = self._block_track(candidate)
            if track is not None:
                uri = candidate
        if expected is not None:
            require_our_stream(player, self.uid, uri, expected,
                               frozenset(self._joined))
        if track is None or uri == self.owned_url:
            return
        self.owned_url = uri
        self.state = self.state.followed(track)
        self._display_progress = None
        self._display_progress_poll_at = None
        print(f'[player] Sonos is on track {track}; following', flush=True)

    def _block_intact(self, player):
        block = self._queue_block
        if not block:
            return False
        last = len(block['urls']) - 1
        return (playing_from_queue(player, propagate_read_errors=True)
                and queue_item_is_ours(player, block['first'] + last, block['urls'][last],
                                       propagate_read_errors=True))

    def _drop_queue_entry(self, player):
        """After our own verified stop/end: remove our block where still ours."""
        block, self._queue_block = self._queue_block, None
        if block is not None:
            remove_queue_block(player, block)

    def verify_existing(self):
        """The speaker we play on; raises RuntimeError if there is none."""
        if self.player is None:
            raise RuntimeError('no Sonos target attached')
        return self.player

    def _fail(self, message):
        self.state = self.state.failed(message)
        print(f'[player] ERROR: {message}; no Sonos STOP sent', flush=True)
        url = self.owned_url
        player = self.player
        self.owned_url = None
        self.player = None
        self.server.close()
        if self._queue_block is not None:
            # Our disc block must not stay behind in the Sonos queue. Each
            # entry is checked before removal, so this never touches the
            # user's own entries; if Sonos is unreachable it stays as is.
            try:
                if player is not None:
                    remove_queue_block(player, self._queue_block)
            except Exception as cleanup:
                print(f'[player] queue cleanup after error failed: {cleanup}', flush=True)
            self._queue_block = None
        # An error here may mean the topology, URI or transport could not be
        # verified. Never issue unjoin on an ambiguous observation.
        if self._joined:
            self._group_uncertain = True
            self._recovery_url = url

    def play(self, uid=None, members=None):
        """Start the selected track on speaker ``uid`` (plus group ``members``).

        Raises:
            ValueError: the player is not stopped.
            RuntimeError: a speaker check or the start failed.
        """
        if self.state.mode != Mode.STOPPED:
            raise ValueError('PLAY requires STOPPED')
        candidate = uid if uid is not None else self.uid
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError('PLAY requires target UID')
        wanted = groups.normalize_members(candidate, members)
        if self._group_uncertain:
            self.resolve_idle_group()
        if self._group_uncertain:
            raise RuntimeError('group cleanup uncertain; manual recovery required')
        retained = bool(self._joined) and candidate == self.uid and wanted == self._group_members
        if self._joined and not retained:
            raise RuntimeError('retained group must be resolved before changing target')
        target = (self._verify_retained_group(candidate) if retained
                  else self._verify_releasing_leftover(candidate, wanted))
        member_players = {}
        if wanted and not retained:
            member_players = self.find_many(sorted(wanted))
            if set(member_players) != set(wanted):
                raise RuntimeError('member discovery incomplete')
            for member_uid, member in member_players.items():
                groups.require_member_ready_releasing(
                    member, member_uid, self.remembered_group())
        host = self.route(target.ip_address) if self.route is not None else self.host
        url = track_stream_url(host, self.port, self.state.selected_track)
        self.server.close()
        # Publish STARTING before the potentially long, synchronous PCM prebuffer.
        # No Sonos command has been sent and ownership is not claimed here.
        self.state = self.state.start_requested()
        self._sonos_read_failure_since = None
        self._display_progress = None
        self._display_progress_poll_at = None
        if self.status_hook is not None:
            self.status_hook()
        try:
            meta = self.metadata_snapshot
            cover = meta.get('cover_file') if isinstance(meta, dict) else None
            cover_path = cover_route(cover) and cover
            # Whole disc as a block in the Sonos queue, unless shuffle/repeat
            # is on or [sonos] use_queue = false.
            block = self.use_queue and queue_allowed(target)
            server_options = {}
            if cover_path:
                server_options['cover'] = cover_path
            if block:
                server_options['disc'] = True
            self.server.start(self.state.selected_track, **server_options)
            player = self._verify_retained_group(candidate) if retained else self.verify(candidate)
            if wanted and not retained:
                for member_uid, member in member_players.items():
                    groups.require_member_ready(member, member_uid)
            if self.route is not None and self.route(player.ip_address) != host:
                raise RuntimeError('route to target changed during startup')
            if not self.server.alive():
                raise RuntimeError('server exited before Sonos PLAY')
            if wanted and not retained:
                try:
                    self._joined = groups.form(player, member_players)
                    self._group_coord = player
                    self._group_members = wanted
                    self._store_group()
                except groups.GroupRollbackIncomplete as rollback:
                    self._joined = rollback.remaining
                    self._group_coord = player
                    self._group_members = wanted
                    self._group_uncertain = True
                    self._recovery_url = None
                    self._store_group()
                    raise
            cover_url = (f'http://{host}:{self.port}{cover_route(cover_path)}'
                         if cover_path else None)
            didl = track_didl(url, meta, self.state.selected_track,
                              album_prefix=self.album_prefix, cover_url=cover_url,
                              duration_seconds=self._toc_duration(self.state.selected_track))
            if block:
                self._enqueue_disc(player, host, meta, cover_url)
            elif didl is not None:
                player.play_uri(url, meta=didl)
            else:
                player.play_uri(url, title=f'SonosCDPlayer Track {self.state.selected_track}')
        except BaseException as exc:
            self.server.close()
            if self._queue_block is not None:
                # Half-added disc block: take back what is verifiably ours.
                try:
                    remove_queue_block(player, self._queue_block)
                except Exception as cleanup:
                    print(f'[player] queue cleanup failed: {cleanup}', flush=True)
                self._queue_block = None
            # A failed/ambiguous play_uri may already have changed the coordinator.
            # Never unjoin after an uncertain Sonos write without URI proof.
            if self._joined and not self._group_uncertain:
                self._group_uncertain = True
                self._recovery_url = None  # play_uri outcome is ambiguous
            self.owned_url, self.player = None, None
            self.state = self.state.failed(f'PLAY failed: {type(exc).__name__}: {exc}')
            raise
        self.uid, self.host = candidate, host
        self._group_members = wanted
        self.player, self.owned_url = player, url
        self.seen_playing = False
        self.last_transport = 'TRANSITIONING'
        self.start_monotonic = time.monotonic()
        self.stopped_since = None

    def pause(self):
        """Pause our own stream."""
        if self.state.mode != Mode.PLAYING or not self.seen_playing or not self.owned_url or not self.server.alive():
            raise ValueError('PAUSE requires confirmed owned PLAYING')
        player = self._follow_group(self.verify_existing())
        self._ownership(player, 'PLAYING')
        self._ownership(player, 'PLAYING')
        player.pause()
        self.state = self.state.paused()
        self.last_transport = 'PAUSED_PLAYBACK'

    def resume(self):
        """Continue our paused stream."""
        if self.state.mode != Mode.PAUSED or not self.seen_playing or not self.owned_url or not self.server.alive():
            raise ValueError('RESUME requires confirmed owned PAUSED')
        player = self._follow_group(self.verify_existing())
        self._ownership(player, 'PAUSED_PLAYBACK')
        self._ownership(player, 'PAUSED_PLAYBACK')
        player.play()
        self.state = self.state.resumed()
        self.last_transport = 'PLAYING'

    def playpause(self, uid=None, members=None):
        """PLAY when stopped, otherwise toggle pause."""
        if self.state.mode == Mode.STOPPED:
            return self.play(uid, members)
        if uid is not None and uid != self.uid:
            raise ValueError('PLAYPAUSE target UID mismatch')
        if self.state.mode == Mode.PLAYING:
            return self.pause()
        if self.state.mode == Mode.PAUSED:
            return self.resume()
        raise ValueError('PLAYPAUSE forbidden in current mode')

    def _stop_owned(self, *, keep_group=False):
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED) or not self.seen_playing or not self.owned_url:
            raise ValueError('STOP/track switch requires confirmed owned playback')
        player = self._follow_group(self.verify_existing())
        expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
        self._ownership(player, expected)
        self._ownership(player, expected)
        player.stop()  # only destructive action
        old_url = self.owned_url
        self.state = self.state.natural_end()
        self.owned_url, self.player = None, None
        self.last_transport = 'STOPPED'
        self.seen_playing = False
        self.server.close()
        if not keep_group:
            # Release while the coordinator still shows our stream as proof;
            # emptying the queue first would change its URI and keep the group.
            self._release_group(owned_url=old_url)
        self._drop_queue_entry(player)
        if not keep_group and self._joined:
            self.resolve_idle_group()     # we just stopped it: it is idle

    def stop(self):
        """Stop our own stream (after checking it twice) and release the group."""
        # If Sonos already stopped during STARTING, release ONLY our local session.
        # Never issue Sonos STOP on this recovery path.
        if self.state.mode == Mode.STARTING and self.owned_url and self.player:
            player = self.verify_existing()
            if player.uid != self.uid:
                raise RuntimeError('target UID changed')
            if not groups.is_expected_group(player, self.uid, self._group_members):
                raise RuntimeError('target group changed; refusing local release')
            state = player.get_current_transport_info()['current_transport_state']
            self.last_transport = state
            if state != 'STOPPED':
                raise ValueError('startup recovery only when Sonos is already STOPPED')
            old_url = self.owned_url
            self.state = self.state.natural_end()
            self.owned_url, self.player = None, None
            self.seen_playing = False
            self.start_monotonic, self.stopped_since = None, None
            self.server.close()
            self._release_group(owned_url=old_url)   # before the queue changes
            self._drop_queue_entry(player)
            return
        self._stop_owned()

    def retarget(self, uid, members=None):
        """Change the speakers of the running playback.

        Same coordinator: members are added or removed while the music plays
        on. Another coordinator: our playback stops there and starts on the
        new speakers at the beginning of the current track.

        Raises:
            ValueError: no own playback (PLAYING or PAUSED) is running.
            RuntimeError: a speaker check failed; for members the playback
                keeps running, for a new coordinator it is in ERROR.
        """
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED) or not self.seen_playing or not self.owned_url:
            raise ValueError('speaker change requires own PLAYING or PAUSED playback')
        wanted = groups.normalize_members(uid, members)
        player = self._follow_group(self.verify_existing())
        if uid == self.uid:
            expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
            self._ownership(player, expected)
            if wanted == frozenset(self._joined):
                return
            try:
                self._joined = groups.change_members(player, self._joined, wanted, self.find_many)
            finally:
                # Whatever happened, remember what Sonos reports now.
                self._adopt_group(player)
            print(f'[player] speakers changed during playback; now {len(self._joined)} member(s)',
                  flush=True)
            return
        print('[player] new coordinator chosen during playback; restarting the track there',
              flush=True)
        self._stop_owned()
        self.play(uid, sorted(wanted))

    def can_reset(self):
        """ERROR whose local session is fully released (see reset())."""
        return (self.state.mode == Mode.ERROR and not self.server.alive()
                and not self.owned_url and self.player is None
                and not self._joined and not self._group_uncertain)

    def reset(self):
        """Clear an already locally released ERROR; never contact or command Sonos."""
        if self.state.mode != Mode.ERROR:
            raise ValueError('RESET requires ERROR')
        if self.server.alive() or self.owned_url or self.player is not None:
            raise RuntimeError('RESET refused: local playback still attached')
        if self._joined or self._group_uncertain:
            raise RuntimeError('RESET refused: unresolved temporary group')
        self.state = self.state.reset_error()
        self.uid = None
        self.last_transport = 'UNKNOWN'
        self.seen_playing = False
        self.start_monotonic = self.stopped_since = None

    def force_stop(self, uid, *, discover=None):
        """Explicit emergency action: stop selected solo speaker without URI proof.

        Never affects a group. Never chooses a device implicitly. No retries.
        The operator intentionally overrides the normal owned-stream restriction.
        """
        from sonoscdplayer.sonos.speaker_checks import find_speaker
        if not uid:
            raise ValueError('force STOP requires explicit target UID')
        if self.owned_url and self.uid != uid:
            raise RuntimeError('another target has active local session; force STOP refused')
        target = (discover or find_speaker)(uid)
        for _ in range(2):
            if target.uid != uid:
                raise RuntimeError('force STOP UID mismatch')
            group = target.group
            members = groups.visible_members(group)
            if (len(members) != 1 or members[0].uid != uid
                    or group.coordinator.uid != uid):
                raise RuntimeError('force STOP blocked: target not a solo coordinator')
        # This action CAN stop unrelated playback on the explicitly selected solo
        # speaker. Caller must request --force; never use it automatically.
        target.stop()
        self.last_transport = 'STOPPED'
        if self.uid == uid and self.owned_url:
            self.server.close()
            self.owned_url, self.player = None, None
            self.seen_playing = False
            self.start_monotonic, self.stopped_since = None, None
            if self.state.mode in (Mode.STARTING, Mode.PLAYING):
                self.state = self.state.natural_end()
            elif self.state.mode == Mode.ERROR:
                self.state = self.state.reset_error()
        # _fail() already detached the original session. A successful, explicit
        # emergency STOP still permits recovery from that released ERROR state.
        if (self.state.mode == Mode.ERROR and not self.server.alive()
                and self.owned_url is None and self.player is None):
            self.reset()

    def change(self, direction):
        """Go to the 'next' or 'previous' track, playing or stopped."""
        if direction not in ('next', 'previous'):
            raise ValueError('invalid direction')
        if self.state.mode == Mode.STOPPED:
            self.state = (self.state.next_track() if direction == 'next'
                          else self.state.previous_track())
            return
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED):
            raise ValueError('track switch requires STOPPED or confirmed owned playback')
        old = self.state.selected_track
        numbers = self.state.track_numbers
        pos = numbers.index(old)
        index = min(pos + 1, len(numbers)-1) if direction == 'next' else max(pos - 1, 0)
        new = numbers[index]
        if new == old:
            print('[player] already at disc boundary', flush=True)
            return
        if self._queue_block:
            self._jump_in_block(new)
            return
        members = self._group_members
        self._stop_owned(keep_group=bool(members))
        self.state = self.state.select(new)
        try:
            self.play(members=members)
        except BaseException:
            # The previous track has already been stopped. The next PLAY may
            # have failed before or after a Sonos write; never blindly unjoin.
            # Preserve every joined member for explicit recovery inspection.
            if self._joined:
                self._group_uncertain = True
                self._recovery_url = None
            raise

    def _jump_in_block(self, track):
        """NEXT/PREVIOUS on the device while our block plays: move within it."""
        if not self.seen_playing or not self.owned_url:
            raise ValueError('track switch requires confirmed owned playback')
        player = self.verify_existing()
        expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
        self._ownership(player, expected)
        self._ownership(player, expected)
        block = self._queue_block
        index = block['tracks'].index(track)
        player.play_from_queue(block['first'] - 1 + index)
        self.owned_url = block['urls'][index]
        self.state = self.state.followed(track)
        if self.state.mode == Mode.PAUSED:
            self.state = self.state.resumed()   # play_from_queue starts playback
        self._display_progress = None
        self._display_progress_poll_at = None
        self.last_transport = 'TRANSITIONING'

    def apply_command(self, line):
        """Carry out a simple command from the service: play, pause, resume,
        playpause, stop, next, previous or "select N".

        Raises:
            ValueError: unknown command.
        """
        tokens = line.strip().lower().split()
        name = tokens[0] if tokens else ''
        if name == 'play' and len(tokens) == 1:
            self.play()
        elif name == 'pause' and len(tokens) == 1:
            self.pause()
        elif name == 'resume' and len(tokens) == 1:
            self.resume()
        elif name == 'playpause' and len(tokens) == 1:
            self.playpause()
        elif name == 'stop' and len(tokens) == 1:
            self.stop()
        elif name in ('next', 'previous') and len(tokens) == 1:
            self.change(name)
        elif name == 'select' and len(tokens) == 2 and tokens[1].isdigit():
            self.state = self.state.select(int(tokens[1]))
        else:
            raise ValueError(f'unknown command: {line.strip()!r}')

    def shutdown_owned(self, policy='stop_owned'):
        """Best-effort shutdown; NEVER stop Sonos without two fresh URI proofs.

        No assumption is made about STARTING, ERROR, a changed group, or a
        changed URI.  The server is closed by close() in the caller's finally.
        """
        if policy not in ('stop_owned', 'leave_playing'):
            raise ValueError('unknown shutdown policy')
        if policy == 'leave_playing':
            return {'action': 'left_playing', 'reason': 'configured policy'}
        if not self.owned_url or self.player is None:
            return {'action': 'nothing_to_stop'}
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED) or not self.seen_playing:
            return {'action': 'not_stopped', 'reason': 'playback never confirmed'}
        try:
            player = self.verify_existing()
            expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
            self._ownership(player, expected)
            self._ownership(player, expected)
            player.stop()
        except Exception as exc:
            return {'action': 'not_stopped',
                    'reason': f'{type(exc).__name__}: {exc}'}
        old_url = self.owned_url
        self.last_transport = 'STOPPED'
        self.state = self.state.natural_end()
        self.owned_url, self.player = None, None
        self.seen_playing = False
        released = self._release_group(owned_url=old_url)   # before the queue changes
        self._drop_queue_entry(player)
        if not released:
            return {'action': 'stopped_owned_group_cleanup_pending'}
        return {'action': 'stopped_owned'}

    @staticmethod
    def sonos_live_status(uid, *, discover=None):
        """Read-only inspection; never attaches or changes playback ownership."""
        from sonoscdplayer.sonos.speaker_checks import find_speaker
        if not isinstance(uid, str) or not uid:
            raise ValueError('explicit UID required')
        player = (discover or find_speaker)(uid)
        if player.uid != uid:
            raise RuntimeError('live status target UID mismatch')
        group = player.group
        members = groups.visible_members(group)
        is_solo = (len(members) == 1 and members[0].uid == uid
                   and group.coordinator.uid == uid)
        transport = player.get_current_transport_info()['current_transport_state']
        uri = player.get_current_track_info().get('uri')
        return {'target_uid': uid, 'transport': transport, 'solo': is_solo,
                'uri_present': bool(uri),
                'player_stream_uri': isinstance(uri, str) and '/track/' in uri and uri.endswith('.wav')}
 
    def close(self):
        """Stop the track server; never sends a Sonos STOP."""
        self.server.close()


def _log_release(outcome):
    """One journal line per group release: how many left, why others stayed."""
    if not outcome:
        return
    counts = {}
    for value in outcome.values():
        counts[value] = counts.get(value, 0) + 1
    print('[sonos] group release: ' + ', '.join(f'{n} {what}' for what, n in sorted(counts.items())),
          flush=True)
