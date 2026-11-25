#!/usr/bin/env python3
"""The player service (sonoscdplayer-player.service).

Started with ``python3 -m sonoscdplayer.player.service``. At startup it
finds the drive and reads the inserted CD, then listens on a local Unix
socket (mode 0600, no network port) for one-line JSON commands from the
display service, the button service and ``player.remote``. A single worker
thread owns the Controller and carries out every command and the regular
refresh, so commands never run concurrently. The status is published for
quick reads without waiting for the worker.

The service never starts playback on its own and never stops Sonos when it
exits. It refuses to remove an existing socket path, even a stale one.
"""
from __future__ import annotations

import argparse
from sonoscdplayer.system import config_paths
import copy
import concurrent.futures
import threading
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import sys
import time
import tomllib

from sonoscdplayer.audio.table_of_contents import read_toc
from sonoscdplayer.drive.disc_startup import inspect_initial_disc
from sonoscdplayer.drive.disc_monitor import DiscMonitor
from sonoscdplayer.metadata.worker import MetadataWorker, matching_policy_from_config
from sonoscdplayer.metadata.worker import describe as describe_metadata
from sonoscdplayer.metadata.local_metadata import read_cdtext
from sonoscdplayer.sonos.track_metadata import album_prefix_from_config, use_queue_from_config
from sonoscdplayer.drive.eject import eject_verified_idle
from sonoscdplayer.player.state import Mode, PlayerState, state_for_disc
from sonoscdplayer.drive.power import DrivePowerManager, load_config
from sonoscdplayer.drive.wake_validation import wait_for_woken_disc
from sonoscdplayer.drive.usb_identity import usb_identity, recovered_device
from sonoscdplayer.player.controller import Controller, TrackServerProcess, routed_source_ip
from sonoscdplayer.player.web_dashboard import DashboardPublisher
from sonoscdplayer.sonos.target_selection import SelectedTarget
from sonoscdplayer.sonos.target_store import PlaybackGroupStore, TargetStore, default_state_dir
from sonoscdplayer.sonos.volume import VolumeControl, parse_volume_command
from sonoscdplayer.sonos.groups import normalize_members
from sonoscdplayer.system.package_paths import SOURCE_ROOT

# Library edits batch up to four fields; all request types remain validated.
MAX_REQUEST = 4096
# Commands whose refusal the display reports ("playback not possible ...").
PLAY_COMMANDS = frozenset(('play', 'play-selected', 'playpause', 'resume'))
COMMANDS = ('status', 'sonos-status --uid UID', 'play --uid UID', 'playpause --uid UID', 'pause', 'resume', 'next', 'previous', 'eject', 'stop', 'stop --force --uid UID', 'select N', 'reset', 'help', 'set-target --uid UID', 'get-target', 'clear-target', 'play-selected', 'inspect-group-recovery', 'resolve-group-recovery', 'volume +N|-N', 'reset-disc')


def maintenance_blocks_playback(request, metadata_worker):
    """Block local playback operations while destructive maintenance runs."""
    if not getattr(metadata_worker, 'destructive_maintenance_pending', False):
        return False
    command = request.get('command') if isinstance(request, dict) else None
    if not isinstance(command, str):
        return False
    command = command.strip().lower()
    return (command in PLAY_COMMANDS | {'reset', 'next', 'previous'} or
            re.fullmatch(r'select [0-9]{1,3}', command) is not None)


def response(controller, *, ok, error=None, commands=None):
    """The JSON reply: ok/error, the status line and the full state snapshot."""
    result = {'ok': ok, 'status': controller.status(),
              'state': controller.status_snapshot()}
    if error is not None:
        result['error'] = error
    if commands is not None:
        result['commands'] = commands
    return result


def handle_command(controller, request, selection=None, eject=None, volume=None):
    """UID is command-scoped; never include it in a reply."""
    if not isinstance(request, dict) or not set(request).issubset({'command', 'uid', 'force', 'members', 'confirm'}) or 'command' not in request:
        return response(controller, ok=False, error='invalid request keys')
    raw = request['command']
    if not isinstance(raw, str) or len(raw) > 80:
        return response(controller, ok=False, error='invalid command')
    command = raw.strip().lower()
    uid = request.get('uid')
    force = request.get('force', False)
    if not isinstance(force, bool):
        return response(controller, ok=False, error='invalid force flag')
    if uid is not None and (not isinstance(uid, str) or not 1 <= len(uid) <= 128 or not re.fullmatch(r'[A-Za-z0-9_:-]+', uid)):
        return response(controller, ok=False, error='invalid UID')
    confirmed = request.get('confirm', None)
    if confirmed is not None and (command != 'resolve-group-recovery' or confirmed is not True):
        return response(controller, ok=False, error='confirmation only accepted for recovery')
    if command == 'resolve-group-recovery' and confirmed is not True:
        return response(controller, ok=False, error='explicit confirmation required')
    members = request.get('members')
    if members is not None:
        if command != 'set-target':
            return response(controller, ok=False, error='members only allowed with set-target')
        try:
            members = tuple(sorted(normalize_members(uid, members)))
        except ValueError:
            return response(controller, ok=False, error='invalid members')
    if force and (command != 'stop' or uid is None):
        return response(controller, ok=False, error='--force permitted only with stop --uid')
    if command in ('play', 'playpause', 'sonos-status', 'set-target') and uid is None:
        return response(controller, ok=False, error='PLAY requires --uid')
    if command not in ('play', 'playpause', 'sonos-status', 'set-target') and not (command == 'stop' and force) and uid is not None:
        return response(controller, ok=False, error='UID only accepted with PLAY')
    if not (command in ('status', 'sonos-status', 'play', 'playpause', 'pause', 'resume', 'next', 'previous', 'eject', 'stop', 'reset', 'help', 'set-target', 'get-target', 'clear-target', 'play-selected', 'inspect-group-recovery', 'resolve-group-recovery')
            or re.fullmatch(r'select [0-9]{1,3}', command)
            or re.fullmatch(r'volume [+-][0-9]{1,2}', command)):
        return response(controller, ok=False, error='unknown command')
    try:
        if command.startswith('volume '):
            if volume is None:
                raise RuntimeError('volume control unavailable')
            level = volume.adjust(controller, selection, parse_volume_command(command))
            reply = response(controller, ok=True)
            reply['volume'] = level
            return reply
        if command == 'help':
            return response(controller, ok=True, commands=COMMANDS)
        if command in ('set-target', 'get-target', 'clear-target'):
            if selection is None:
                raise RuntimeError('target selection unavailable')
            if command == 'set-target':
                selection.set(controller, uid, members)
                controller.target_rev = getattr(controller, "target_rev", 0) + 1
            elif command == 'clear-target':
                selection.clear(controller)
            reply = response(controller, ok=True)
            reply['target_selection'] = selection.summary()
            if command == 'get-target':
                reply['target_selection']['speakers'] = selection.speakers()
            return reply
        if command in ('inspect-group-recovery', 'resolve-group-recovery'):
            if command == 'inspect-group-recovery':
                inspected = controller.inspect_group_recovery()
            else:
                inspected = controller.resolve_group_recovery()
            reply = response(controller, ok=True)
            reply['group_recovery'] = inspected
            return reply
        if command == 'eject':
            if eject is None:
                raise RuntimeError('EJECT unavailable: no verified disc monitor')
            eject()
            return response(controller, ok=True)
        if command == 'play-selected':
            if selection is None or selection.coordinator_uid() is None:
                raise ValueError('no confirmed target')
            # This is the ONLY new route for group PLAY. It requires a separate
            # explicit command; selecting targets cannot start music.
            if controller.state.mode == Mode.ERROR and controller.can_reset():
                # PLAY after a released error: clear it and try again.
                controller.reset()
            controller.play(selection.coordinator_uid(),
                            members=selection.member_uids())
            return response(controller, ok=True)
        if command == 'sonos-status':
            reply = response(controller, ok=True)
            reply['live_sonos'] = controller.sonos_live_status(uid)
            return reply
        if command == 'stop' and force:
            controller.force_stop(uid)
        elif command == 'reset':
            controller.reset()
        elif command == 'play':
            controller.play(uid)
        elif command == 'playpause':
            controller.playpause(uid)
        elif command != 'status':
            controller.apply_command(command)
        return response(controller, ok=True)
    except Exception as exc:
        if command == 'eject':
            # Keep physical diagnostic server-side; never reveal Sonos target UIDs.
            detail = str(exc)
            if not detail.startswith('EJECT '):
                detail = ('errno=' + str(exc.errno) if isinstance(exc, OSError)
                          and exc.errno is not None else type(exc).__name__)
            print('[eject] EJECT rejected: ' + detail, flush=True)
        return response(controller, ok=False, error=f'{type(exc).__name__}: {exc}')




def idle_stop_unverified(power):
    """An attempted but unacknowledged STOP requires intervention."""
    policy = getattr(power, 'idle_policy', None)
    return (
        policy is not None
        and getattr(policy, 'stop_attempted', False)
        and not getattr(policy, 'spun_down', False)
    )



def _diagnosed_wake(power, monitor, *, source):
    """One existing wake, with passive verification diagnostics."""
    diagnostics = {}
    started = time.monotonic()

    def verify(device):
        diagnostics["verification_entered"] = True
        return wait_for_woken_disc(
            monitor, device, diagnostics=diagnostics
        )

    ok = power.wake(verify=verify)
    elapsed = time.monotonic() - started

    # Never print USB serials, complete TOCs or Sonos target identifiers.
    print(
        "[drive-wake] "
        f"source={source} "
        f"ok={ok} "
        f"elapsed_seconds={elapsed:.2f} "
        f"verification_entered={diagnostics.get('verification_entered', False)} "
        f"attempts={diagnostics.get('attempts', 0)} "
        f"stage={diagnostics.get('stage', 'not_entered')} "
        f"timeout={diagnostics.get('timeout_reached', False)} "
        f"check={diagnostics.get('check', '-')} "
        f"exception={diagnostics.get('exception', '-')} "
        f"media_code={diagnostics.get('media_code', '-')} "
        f"observed_tracks={diagnostics.get('observed_tracks', '-')} "
        f"expected_tracks={diagnostics.get('expected_tracks', '-')}",
        flush=True,
    )

    # The manager's original last_error remains available in the
    # drive_power status snapshot; diagnostics do not modify it.
    return ok



def wake_before_new_play(power, monitor, request, *, stopped, publish_wake=None):
    """Wake and verify before the existing explicit PLAY media guard.

    Never contacts Sonos. An unverified wake blocks the requested PLAY.
    """
    if (
        not stopped
        or not isinstance(request, dict)
        or request.get('command') not in ('play', 'play-selected', 'playpause')
        or power is None
        or power.idle_policy is None
    ):
        return True

    power.activity()

    if idle_stop_unverified(power):
        return False

    if not power.idle_policy.play_must_wait:
        return True

    # An old-style player without a DiscMonitor cannot safely verify
    # the post-wake medium under this new feature.
    if monitor is None:
        return False

    if publish_wake is not None:
        publish_wake()

    return _diagnosed_wake(power, monitor, source="play")



def process_drive_activity(power, monitor, *, publish_wake=None):
    """Record physical activity; verify wake without any Sonos transport."""
    if power is None or power.idle_policy is None:
        return True

    power.activity()

    if idle_stop_unverified(power):
        return False

    if not power.idle_policy.play_must_wait:
        return True

    if monitor is None:
        return False

    if publish_wake is not None:
        publish_wake()

    return _diagnosed_wake(power, monitor, source="activity")


def disc_allows_new_play(controller, request, monitor):
    """Only explicit new PLAY needs physical TOC revalidation.

    PAUSE/RESUME and status remain side-effect free. A changed but
    successfully validated CD keeps the explicit PLAY authorized; unsafe
    or unreadable media never contacts Sonos on this path.
    """
    return (monitor is None or not isinstance(request, dict)
            or request.get('command') not in ('play', 'play-selected', 'playpause')
            or controller.state.mode not in (Mode.STOPPED, Mode.ERROR)
            or monitor.validate_before_play(controller))


class StatusPublisher:
    """Latest status reply, kept for "status" requests while the worker is busy.

    Only a copy of the state; it never allows a Sonos action.
    """
    def __init__(self, controller, power=None):
        self._lock = threading.Lock()
        self._reply = None
        self.publish(controller, power)

    def publish(self, controller, power=None):
        """Store the current status (called by the worker only)."""
        reply = response(controller, ok=True)
        if power is not None:
            reply['state']['drive_power'] = power.snapshot(reader_active=controller.server.reader_active())
        with self._lock:
            self._reply = copy.deepcopy(reply)

    def read(self):
        """A copy of the stored status reply."""
        with self._lock:
            return copy.deepcopy(self._reply)


def read_request(conn):
    """Read one JSON request line (at most MAX_REQUEST bytes); raises ValueError."""
    conn.settimeout(2)
    data = bytearray()
    while len(data) <= MAX_REQUEST:
        chunk = conn.recv(min(128, MAX_REQUEST + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b'\n' in chunk:
            break
    if len(data) > MAX_REQUEST or b'\n' not in data:
        raise ValueError('request too long or unterminated')
    return json.loads(bytes(data).split(b'\n', 1)[0].decode('utf-8'))


def send_reply(conn, reply):
    """Send the reply as one JSON line and close the connection."""
    try:
        conn.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode())
    except OSError:
        pass
    finally:
        conn.close()


def restore_drive_for_explicit_action(controller, power, original_id, original_toc):
    """Rebind only the original USB drive/disc on an explicit idle action."""
    from sonoscdplayer.player.state import Mode
    if controller.state.mode not in (Mode.STOPPED, Mode.ERROR):
        return False
    if (controller.owned_url or controller.server.alive() or controller._joined
            or getattr(controller, '_group_uncertain', False)):
        return False
    old = controller.server.device
    if Path(old).exists():
        if original_id is None or usb_identity(old) == original_id:
            return False
    if original_id is None:
        raise RuntimeError('original USB identity unavailable; restart required')
    new = recovered_device(original_id)
    if usb_identity(new) != original_id:
        raise RuntimeError('replacement USB drive identity changed')
    if read_toc(new) != original_toc:
        raise RuntimeError('reconnected optical drive contains different or unreadable disc')
    if power is not None:
        power.rebind_device(new, reader_active=controller.server.reader_active())
    controller.server.device = new
    print('[drive] verified USB reconnect; reader and keepalive rebound', flush=True)
    return True



def disc_poll_allowed(power):
    """Do not probe media after an attempted idle STOP or during wake."""
    policy = getattr(power, 'idle_policy', None) if power is not None else None
    if policy is None:
        return True
    return not (
        policy.stop_attempted
        or policy.spun_down
        or policy.wake_in_progress
        or policy.wake_fault_latched
    )



def handle_disc_removal(controller, monitor, power, now):
    """The CD was taken out with the drive's own button while playing or asleep.

    Checked only in these two states, where the normal disc poll does not
    run. During own playback the playback is stopped at once (group
    released); while the drive sleeps the idle state is reset, the disc is
    gone. Returns None (not checked), 'present', 'stopped' or 'woke'.
    """
    playing = (bool(controller.owned_url)
               and controller.state.mode in (Mode.PLAYING, Mode.PAUSED))
    asleep = not disc_poll_allowed(power)
    if not (playing or asleep):
        return None
    try:
        removed = monitor.media_removed()
    except (OSError, RuntimeError, ValueError):
        return 'present'
    if not removed:
        return 'present'
    action = 'woke'
    if playing:
        print('[disc] CD taken out during playback; stopping', flush=True)
        try:
            controller.stop()
        except Exception as exc:
            print(f'[disc] stop after removal failed: {type(exc).__name__}: {exc}', flush=True)
        action = 'stopped'
    if asleep and power is not None and power.idle_policy is not None:
        power.idle_policy.reset_after_eject(now)
        print('[disc] tray opened while the drive slept', flush=True)
    return action


def eject_blocked_by_idle_policy(power):
    """Fail closed if the motor state has not been safely restored.

    Two stuck states are exceptions, because the disc must stay removable: a
    latched wake fault, and a STOP that was sent but never acknowledged. The
    EJECT path itself re-verifies drive identity, media and idleness under the
    device lock and refuses otherwise. PLAY stays blocked in both.

    No device probing, START, EJECT, or Sonos operations here.
    """
    policy = getattr(power, 'idle_policy', None)
    if policy is None:
        return False
    if policy.wake_in_progress:
        return True
    if policy.wake_fault_latched or idle_stop_unverified(power):
        return False
    return bool(policy.stop_attempted or policy.spun_down)


def eject_and_recover(controller, monitor, power):
    """Verified EJECT; if it succeeded from a stuck idle state, clear that state.

    While a wake fault is latched or a STOP is unconfirmed the disc monitor
    stays blocked, so without this a newly inserted disc would never be detected.
    """
    policy = getattr(power, 'idle_policy', None)
    stuck = bool(policy is not None
                 and (policy.wake_fault_latched or idle_stop_unverified(power)))
    result = eject_verified_idle(controller, monitor)
    if stuck:
        power.clear_fault_after_eject()
    return result


def _current_disc(disc_monitor, drive_toc, drive_cdtext):
    """Verified TOC and the CD-TEXT read with it (None when unknown).

    While CD-TEXT is still being read, the metadata lookup waits for it.
    """
    if disc_monitor is not None:
        if getattr(disc_monitor, 'cdtext_pending', False):
            return None, None
        return disc_monitor.loaded_toc, disc_monitor.loaded_cdtext
    return drive_toc, drive_cdtext


def serve(controller, socket_path, *, poll_seconds=0.5, power=None, drive_id=None, drive_toc=None, disc_monitor=None, target_store=None, metadata_worker=None, drive_cdtext=None, stop_event=None):
    """Listen on ``socket_path`` and run the worker until SIGTERM/SIGINT.

    The optional arguments connect drive power management, the USB identity
    and TOC of the drive, the disc monitor, the remembered speaker selection
    and the metadata worker. A supplied stop_event is for controlled test
    teardown; production runs without one.
    """
    path = Path(socket_path).expanduser().absolute()
    if not path.parent.is_dir():
        raise ValueError('socket parent directory does not exist')
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'refusing existing socket/path: {path}')
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    original_umask = os.umask(0o177)
    try:
        server.bind(str(path))
        # Listen at once: clients wait for the socket file, and a connect
        # between bind() and listen() would be refused. Requests queue in the
        # backlog until the loop below accepts them.
        server.listen(16)
    finally:
        os.umask(original_umask)
    bound_inode = os.lstat(path).st_ino
    publisher = StatusPublisher(controller, power)
    selection = SelectedTarget(store=target_store)
    if selection.restore():
        print('[target] remembered speaker selection restored; no Sonos action until PLAY', flush=True)
    dashboard = DashboardPublisher(controller, selection)

    def follow_app(uid, members):
        selection.follow(uid, members)
        controller.target_rev += 1
        print('[target] selection follows the group changed in the Sonos app', flush=True)
    if hasattr(controller, 'selection_hook'):
        controller.selection_hook = follow_app
    volume = VolumeControl()
    if hasattr(controller, 'status_hook'):
        controller.status_hook = lambda: publisher.publish(controller, power)
    pending = []
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        server.setblocking(False)
        print(f'[service] local socket ready: {path} (0600); responsive cached status', flush=True)
        # Only this one worker ever touches controller, Sonos, reader and drive power.
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix='player-control') as executor:
            def operate(request):
                # Exact, dedicated RPC: activity alone never authorizes
                # playback, transport, target changes or EJECT.
                if isinstance(request, dict) and request == {'command': 'drive-activity'}:
                    ok = process_drive_activity(
                        power, disc_monitor,
                        publish_wake=lambda: publisher.publish(
                            controller, power
                        ),
                    )
                    reply = response(
                        controller, ok=ok,
                        error=None if ok else 'drive wake unverified',
                    )
                    if power is not None:
                        reply['state']['drive_power'] = power.snapshot(
                            reader_active=controller.server.reader_active()
                        )
                    publisher.publish(controller, power)
                    return reply

                # A stopped drive must be woken and verified BEFORE the
                # DiscMonitor's existing PLAY-boundary TOC validation.
                if not wake_before_new_play(
                    power, disc_monitor, request,
                    # A released ERROR is retried like STOPPED (wake the drive first).
                    stopped=(power is not None and controller.state.mode in (Mode.STOPPED, Mode.ERROR)),
                    publish_wake=lambda: publisher.publish(
                        controller, power
                    ),
                ):
                    reply = response(
                        controller, ok=False,
                        error='drive wake unverified; PLAY blocked',
                    )
                    publisher.publish(controller, power)
                    return reply
                # Check the *physical* TOC immediately before a new PLAY. The
                # 3-second cached monitor status must not authorize stale media.
                if not disc_allows_new_play(controller, request, disc_monitor):
                    reply = response(controller, ok=False,
                                     error='disc unavailable, unreadable or unverified; inspect status before PLAY')
                    publisher.publish(controller, power)
                    return reply
                if (disc_monitor is None and drive_toc is not None
                        and isinstance(request, dict)
                        and request.get('command') in ('play', 'play-selected', 'playpause', 'reset')):
                    try:
                        restore_drive_for_explicit_action(controller, power, drive_id, drive_toc)
                    except Exception as exc:
                        reply = response(controller, ok=False, error=f'drive unavailable: {type(exc).__name__}: {exc}')
                        publisher.publish(controller, power)
                        return reply
                if (
                    isinstance(request, dict)
                    and isinstance(request.get('command'), str)
                    and request['command'].strip().lower() == 'eject'
                    and eject_blocked_by_idle_policy(power)
                ):
                    reply = response(
                        controller, ok=False,
                        error='EJECT blocked: idle drive state unverified',
                    )
                    publisher.publish(controller, power)
                    return reply

                if metadata_worker is not None:
                    current_toc, current_text = _current_disc(disc_monitor, drive_toc, drive_cdtext)
                    if metadata_worker.observe(current_toc, current_text):
                        controller.metadata_snapshot = None

                if isinstance(request, dict) and request.get('command') == 'edit-metadata':
                    # Validation and scheduling only; SQLite I/O stays off this worker.
                    if set(request) - {'command', 'token', 'field', 'track', 'value'} or not all(
                            key in request for key in ('token', 'field', 'value')):
                        reply = response(controller, ok=False, error='invalid metadata edit keys')
                    elif metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            metadata_worker.enqueue_edit(token=request['token'],
                                field=request['field'], track=request.get('track'),
                                value=request['value'])
                            reply = response(controller, ok=True)
                            reply['queued'] = True
                        except (ValueError, RuntimeError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply

                if isinstance(request, dict) and request.get('command') in (
                        'lookup-releases', 'select-release', 'release-detail'):
                    name = request['command']
                    if metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            if name == 'lookup-releases' and set(request) == {'command', 'token'}:
                                metadata_worker.enqueue_release_search(token=request['token'])
                                reply = response(controller, ok=True)
                                reply['queued'] = True
                            elif name == 'select-release' and set(request) == {
                                    'command', 'token', 'release_id', 'keep_manual'}:
                                metadata_worker.enqueue_release_selection(
                                    token=request['token'], release_id=request['release_id'],
                                    keep_manual=request['keep_manual'])
                                reply = response(controller, ok=True)
                                reply['queued'] = True
                            elif name == 'release-detail' and set(request) == {
                                    'command', 'token', 'release_id'}:
                                detail = metadata_worker.release_detail_for(
                                    token=request['token'], release_id=request['release_id'])
                                reply = response(controller, ok=True)
                                reply['release_detail'] = detail
                            else:
                                reply = response(controller, ok=False, error='invalid release request')
                        except (ValueError, RuntimeError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply

                if isinstance(request, dict) and request.get('command') in ('upload-cover', 'remove-cover'):
                    # Only validate and queue; image processing and file I/O stay off the player thread.
                    if metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            if request['command'] == 'upload-cover' and set(request) == {
                                    'command', 'token', 'upload_id'}:
                                metadata_worker.enqueue_cover(token=request['token'],
                                                              upload_id=request['upload_id'])
                                reply = response(controller, ok=True)
                                reply['queued'] = True
                            elif request['command'] == 'remove-cover' and set(request) == {'command', 'token'}:
                                metadata_worker.enqueue_cover(token=request['token'], remove=True)
                                reply = response(controller, ok=True)
                                reply['queued'] = True
                            else:
                                reply = response(controller, ok=False, error='invalid cover request')
                        except (ValueError, RuntimeError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply

                if isinstance(request, dict) and request.get('command') == 'library-maintenance':
                    # No SQLite or cover I/O on the serialized player worker.
                    if metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            from sonoscdplayer.metadata.library_maintenance import validate_maintenance
                            required = {'command', 'action'}
                            if set(request) not in (required, required | {'tokens'}, required | {'confirmation'}):
                                raise ValueError('invalid maintenance request')
                            action, tokens, confirmation = validate_maintenance(
                                request['action'], request.get('tokens'), request.get('confirmation'))
                            if action != 'stats':
                                mode = getattr(getattr(controller, 'state', None), 'mode', None)
                                if (mode != Mode.STOPPED or getattr(controller, 'owned_url', None) or
                                        getattr(controller, '_joined', False) or
                                        getattr(controller, '_group_uncertain', False)):
                                    raise ValueError('stop playback and finish group cleanup first')
                            metadata_worker.enqueue_library_maintenance(
                                action=action, tokens=list(tokens) if action == 'bulk-delete' else None,
                                confirmation=confirmation)
                            reply = response(controller, ok=True)
                            reply['queued'] = True
                        except (ValueError, RuntimeError, TypeError, KeyError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply
                if isinstance(request, dict) and request.get('command') == 'library-manage':
                    # Only scheduling and validation on the serialized player worker.
                    if metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            from sonoscdplayer.metadata.library_manage import validate_manage
                            required = {'command', 'token', 'action'}
                            if set(request) not in (required, required | {'changes'}):
                                raise ValueError('invalid library manage fields')
                            token, action, changes = validate_manage(
                                request['token'], request['action'], request.get('changes'))
                            metadata_worker.enqueue_library_manage(
                                token=token, action=action,
                                changes=([{'field': f, **({'track': n} if n else {}), 'value': v}
                                          for f, n, v in changes] if action == 'edit' else None))
                            reply = response(controller, ok=True)
                            reply['queued'] = True
                        except (ValueError, RuntimeError, TypeError, KeyError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply

                if isinstance(request, dict) and request.get('command') in ('library-query', 'library-detail'):
                    # Query scheduling only; SQLite runs in the metadata executor.
                    if metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            if request['command'] == 'library-query' and set(request) == {
                                    'command', 'query', 'offset', 'limit'}:
                                metadata_worker.enqueue_library(query=request['query'],
                                    offset=request['offset'], limit=request['limit'])
                            elif request['command'] == 'library-detail' and set(request) == {
                                    'command', 'token'}:
                                metadata_worker.enqueue_library(token=request['token'])
                            else:
                                raise ValueError('invalid library request')
                            reply = response(controller, ok=True)
                            reply['queued'] = True
                        except (ValueError, RuntimeError) as exc:
                            reply = response(controller, ok=False, error=str(exc))
                    publisher.publish(controller, power)
                    return reply

                if isinstance(request, dict) and request.get('command') == 'reset-disc':
                    # forget the inserted CD's stored metadata and look it
                    # up again. No drive or Sonos access; playback is not touched.
                    if set(request) != {'command'}:
                        reply = response(controller, ok=False, error='invalid request keys')
                    elif metadata_worker is None:
                        reply = response(controller, ok=False, error='metadata unavailable')
                    else:
                        try:
                            metadata_worker.reset_current()
                            controller.metadata_snapshot = None
                            print('[metadata] entry of the inserted CD reset; looking it up again',
                                  flush=True)
                            reply = response(controller, ok=True)
                        except Exception as exc:
                            reply = response(controller, ok=False,
                                             error=f'{type(exc).__name__}: {exc}')
                    publisher.publish(controller, power)
                    return reply

                # Volume control is passed only to volume requests, so the
                # established command path stays unchanged for all others.
                extra = ({'volume': volume} if isinstance(request, dict)
                         and isinstance(request.get('command'), str)
                         and request['command'].strip().lower().startswith('volume ')
                         else {})
                reply = handle_command(
                    controller, request, selection,
                    eject=(lambda: eject_and_recover(controller, disc_monitor, power)
                           if disc_monitor is not None else None),
                    **extra)
                publisher.publish(controller, power)
                return reply

            unrecorded = operate

            def operate(request):
                command = request.get('command') if isinstance(request, dict) else None
                if maintenance_blocks_playback(request, metadata_worker):
                    # Reject before CD-TEXT cancellation, drive wake or Sonos I/O.
                    reply = response(controller, ok=False, error='maintenance in progress')
                    publisher.publish(controller, power)
                else:
                    if (disc_monitor is not None and getattr(disc_monitor, 'cdtext_pending', False)
                            and isinstance(command, str)
                            and command.strip().lower() in PLAY_COMMANDS | {'eject'}):
                        disc_monitor.cancel_cdtext()     # the drive is needed now
                    reply = unrecorded(request)
                if (reply.get('ok') is False and isinstance(command, str)
                        and command.strip().lower() in PLAY_COMMANDS):
                    controller.note_rejection(command.strip().lower(), reply.get('error'))
                    reply['state'] = controller.status_snapshot()
                    publisher.publish(controller, power)
                return reply

            # Retry only when the old optical path disappears/changes. Avoid
            # probing healthy drives and preserve the normal keepalive cadence.
            next_drive_recovery = 0.0

            next_disc_check = 0.0
            next_removal_check = 0.0

            def check_removal(now):
                nonlocal next_removal_check, next_disc_check
                if disc_monitor is None or now < next_removal_check:
                    return
                action = handle_disc_removal(controller, disc_monitor, power, now)
                if action is not None:
                    next_removal_check = now + 2.0
                if action in ('stopped', 'woke'):
                    next_disc_check = 0.0          # let the normal poll report it now

            def refresh():
                nonlocal next_drive_recovery, next_disc_check, drive_toc
                from sonoscdplayer.player.state import Mode
                if (disc_monitor is None and drive_toc is not None and power is not None
                        and controller.state.mode in (Mode.STOPPED, Mode.ERROR)
                        and not controller.owned_url and not controller.server.alive()
                        and not controller._joined and not controller._group_uncertain
                        and not controller.server.reader_active()):
                    old = controller.server.device
                    missing = not Path(old).exists()
                    changed = (not missing and drive_id is not None
                               and usb_identity(old) != drive_id)
                    now = time.monotonic()
                    if (missing or changed) and now >= next_drive_recovery:
                        next_drive_recovery = now + max(10.0, power.config['interval'])
                        try:
                            # Legacy recovery path: this whole block only runs
                            # without a DiscMonitor (the monitor rebinds itself).
                            restore_drive_for_explicit_action(
                                controller, power, drive_id, drive_toc)
                        except (OSError, RuntimeError, ValueError) as exc:
                            print(f'[drive] idle USB recovery deferred: {type(exc).__name__}: {exc}',
                                  flush=True)
                controller.refresh()
                now = time.monotonic()
                check_removal(now)
                # Safe status inspection only. NEVER touch optical media while
                # reader/stream/group ownership is attached.
                idle_drive_poll_blocked = not disc_poll_allowed(power)
                if (
                    disc_monitor is not None
                    and now >= next_disc_check
                    and not idle_drive_poll_blocked
                ):
                    next_disc_check = now + 3.0
                    try:
                        changed = disc_monitor.poll(controller)
                        if changed:
                            drive_toc = disc_monitor.loaded_toc
                            controller.display_toc = drive_toc
                            print(f'[disc] {controller.disc_status.mode.value}', flush=True)
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f'[disc] monitor deferred: {type(exc).__name__}: {exc}', flush=True)
                if power is not None and (controller.disc_status is None or
                                          controller.disc_status.mode.value == 'READY'):
                    power.tick(
                        reader_active=controller.server.reader_active(),
                        playback_active=(
                            controller.state.mode != Mode.STOPPED
                            or bool(controller.owned_url)
                            or controller.server.alive()
                        ),
                        group_cleanup_pending=bool(
                            controller._joined or controller._group_uncertain
                        ),
                    )
                if metadata_worker is not None:
                    current_toc, current_text = _current_disc(disc_monitor, drive_toc, drive_cdtext)
                    if metadata_worker.observe(current_toc, current_text):
                        controller.metadata_snapshot = None
                    if metadata_worker.poll(current_toc):
                        controller.metadata_snapshot = metadata_worker.snapshot
                        print(describe_metadata(controller.metadata_snapshot), flush=True)
                publisher.publish(controller, power)
                dashboard.publish(controller, selection)

            busy = None
            next_refresh = time.monotonic()
            while stop_event is None or not stop_event.is_set():
                for conn, future in pending[:]:
                    if future.done():
                        pending.remove((conn, future))
                        try:
                            reply = future.result()
                        except Exception as exc:
                            reply = {'ok': False, 'error': f'worker failure: {type(exc).__name__}'}
                        send_reply(conn, reply)
                if busy is None or busy.done():
                    busy = None
                    if time.monotonic() >= next_refresh:
                        busy = executor.submit(refresh)
                        next_refresh = time.monotonic() + poll_seconds
                readable, _, _ = select.select([server], [], [], min(poll_seconds, 0.1))
                if not readable:
                    continue
                conn, _ = server.accept()
                try:
                    request = read_request(conn)
                    # Cached status is served while PLAY waits for the CD spindle.
                    # No synchronous controller read and no new Sonos checks.
                    if isinstance(request, dict) and request == {'command': 'status'}:
                        send_reply(conn, publisher.read())
                    elif isinstance(request, dict) and request == {'command': 'dashboard'}:
                        send_reply(conn, dashboard.read())
                    elif isinstance(request, dict) and request == {'command': 'settings-status'}:
                        runtime = getattr(controller, 'playback_runtime_settings', None)
                        send_reply(conn, {'ok': True, 'settings': runtime.copy()}
                                   if isinstance(runtime, dict) else {'ok': False, 'error': 'unavailable'})
                    elif isinstance(request, dict) and request == {'command': 'library'}:
                        send_reply(conn, metadata_worker.library_snapshot() if metadata_worker is not None
                                   else {'ok': False, 'error': 'metadata unavailable'})
                    elif sum(1 for _, f in pending if not f.done()) >= MAX_WAITING_COMMANDS:
                        # While a long PLAY runs, do not collect button presses
                        # that would all run late.
                        send_reply(conn, {'ok': False, 'error': 'busy: try again'})
                    else:
                        pending.append((conn, executor.submit(run_if_still_wanted,
                                                              operate, conn, request)))
                except (ValueError, UnicodeError, socket.timeout, OSError) as exc:
                    send_reply(conn, {'ok': False, 'error': f'bad request: {type(exc).__name__}'})
    finally:
        dashboard.close()
        if hasattr(controller, 'status_hook'):
            controller.status_hook = None
        for conn, _ in pending:
            conn.close()
        server.close()
        try:
            current = os.lstat(path)
            if stat.S_ISSOCK(current.st_mode) and current.st_ino == bound_inode:
                path.unlink()
        except FileNotFoundError:
            pass


#: Commands that may wait for the single worker; more are answered "busy".
MAX_WAITING_COMMANDS = 4


def client_gone(conn):
    """True if the client closed its connection (it gave up waiting)."""
    timeout = conn.gettimeout()
    try:
        conn.setblocking(False)
        return conn.recv(1, socket.MSG_PEEK) == b''
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True
    finally:
        conn.settimeout(timeout)


def run_if_still_wanted(operate, conn, request):
    """Run a queued command only if its client still waits for the answer.

    A command whose client already timed out is not executed late (for
    example a volume press that waited behind a slow PLAY).
    """
    if client_gone(conn):
        return {'ok': False, 'error': 'not executed: the client stopped waiting'}
    return operate(request)


def refuse_existing_control_socket(socket_path):
    """Fail early before any drive access; serve() still checks before bind."""
    path = Path(socket_path).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'refusing existing socket/path: {path}')


def _terminate(_signum, _frame):
    raise KeyboardInterrupt


def install_sigterm_handler():
    """Turn systemd's SIGTERM (stop, reboot, poweroff) into the clean shutdown path.

    Without this the process dies at once and ``shutdown_owned`` never runs, so a
    system poweroff could leave a CD stream URI on the Sonos speaker.
    """
    return signal.signal(signal.SIGTERM, _terminate)


def run(argv=None):
    """Command line of the service: read the configuration, find the drive, serve."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', default='auto')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--config', default=str(config_paths.player_config()))
    ap.add_argument('--socket', default='/tmp/sonoscdplayer-control.sock')
    ap.add_argument('--state-dir', default=str(default_state_dir()),
                    help='where the last speaker selection is remembered '
                         '(default: $STATE_DIRECTORY or /var/lib/sonoscdplayer)')
    ns = ap.parse_args(argv)
    if ns.device != 'auto' and not re.fullmatch(r'/dev/sr\d+', ns.device):
        ap.error('invalid device')
    base = SOURCE_ROOT
    conf = Path(ns.config)
    if not conf.is_absolute():
        conf = base / conf
    if not conf.is_file():
        ap.error('config file not found')
    ctrl = None
    power = None
    metadata_worker = None
    shutdown_policy = 'stop_owned'
    previous_sigterm = (install_sigterm_handler()
                        if threading.current_thread() is threading.main_thread() else None)
    try:
        refuse_existing_control_socket(ns.socket)
        disc_status, toc = inspect_initial_disc(requested_device=ns.device)
        device = disc_status.device or '/dev/sr0'  # placeholder, never opened without a READY TOC
        identity = usb_identity(device) if disc_status.device is not None else None
        state = state_for_disc(toc, 1) if toc is not None else PlayerState()
        with conf.open('rb') as handle:
            settings = tomllib.load(handle)
            shutdown_policy = settings.get('service', {}).get('shutdown_policy', 'stop_owned')
            startup = settings.get('audio_startup', {})
            if not isinstance(startup, dict):
                raise ValueError('audio_startup must be a TOML table')
            startup_enabled = startup.get('prebuffer_before_sonos', True)
            startup_timeout = startup.get('pcm_timeout_seconds', 45)
            if type(startup_enabled) is not bool or type(startup_timeout) not in (int, float) or not 5 <= startup_timeout <= 120:
                raise ValueError('invalid audio_startup settings')
        if shutdown_policy not in ('stop_owned', 'leave_playing'):
            raise ValueError('invalid service.shutdown_policy')
        from sonoscdplayer.player.playback_settings import merged, FIELDS as PLAYBACK_FIELDS
        settings, _ = merged(settings, state_dir=ns.state_dir)
        effective_buffer = settings['streaming']['max_buffer_mib']
        ctrl = Controller(state, None, None, ns.port,
                          TrackServerProcess(base, device, conf, ns.port,
                                        startup_prebuffer=startup_enabled,
                                        startup_timeout=float(startup_timeout),
                                        max_buffer_mib=effective_buffer), route=routed_source_ip)
        ctrl.playback_runtime_settings = {
            field: settings[field.split('.')[0]][field.split('.')[1]]
            for field in PLAYBACK_FIELDS
        }
        ctrl.disc_status = disc_status
        ctrl.album_prefix = album_prefix_from_config(settings)
        ctrl.use_queue = use_queue_from_config(settings)
        playback = settings.get('playback', {})
        if not isinstance(playback, dict) or type(playback.get('continue', True)) is not bool:
            raise ValueError('playback.continue must be true or false')
        ctrl.auto_advance = playback.get('continue', True)
        ctrl.display_toc = toc  # existing verified TOC, read-only display metadata
        # Metadata is strictly best-effort, never a prerequisite for playback.
        # No CD commands in this worker; only cached TOC, local DB and network.
        # CD-TEXT is read by the player itself, once, right after a verified TOC.
        cdtext_reader = None
        startup_cdtext = None
        try:
            meta = settings.get('metadata', {})
            if not isinstance(meta, dict):
                raise ValueError('metadata must be a TOML table')
            if meta.get('enabled', True) is True:
                musicbrainz = meta.get('musicbrainz', {})
                if not isinstance(musicbrainz, dict):
                    raise ValueError('metadata.musicbrainz must be a table')
                timeout = musicbrainz.get('timeout_seconds', 8)
                if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
                    raise ValueError('invalid metadata.musicbrainz.timeout_seconds')
                # Do not make requests when online metadata is disabled.
                online = (meta.get('online_lookup', True) is True
                          and musicbrainz.get('enabled', True) is True)
                if meta.get('read_cd_text', True) is True:
                    cdtext_reader = read_cdtext
                    # The startup CD-TEXT is read in the background by the
                    # disc monitor (below), so the service is ready at once.
                metadata_worker = MetadataWorker(
                    cache_path=Path(ns.state_dir) / 'metadata.sqlite3',
                    cover_dir=Path(ns.state_dir) / 'covers',
                    online=online, timeout=timeout,
                    matching_policy=matching_policy_from_config(meta),
                    # The OLED shows no covers yet; Sonos shows album art.
                    display_supports_cover=False, sonos_uses_cover=True,
                    cover_state_dir=ns.state_dir)
                # With CD-TEXT the lookup starts once the monitor has read it.
                metadata_worker.observe(toc if cdtext_reader is None else None, None)
        except (OSError, ValueError) as exc:
            print(f'[metadata] disabled: {type(exc).__name__}', flush=True)

        # Never issue start/keepalive motor commands when no readable disc was
        # selected. Disc changes while idle are handled by disc_monitor.
        if toc is not None:
            power = DrivePowerManager(device, load_config(conf))
            power.start()
        print(f'[disc] service ready; disc={disc_status.mode.value}; '
              'no Sonos action until explicit PLAY', flush=True)
        def on_verified_rebind(new_device):
            # Same original USB serial, validated by DiscMonitor; only idle.
            if power is not None:
                power.rebind_device(new_device,
                                    reader_active=ctrl.server.reader_active())
            ctrl.server.device = new_device
            print(f'[drive] verified USB identity rebind: {new_device}', flush=True)

        monitor = (DiscMonitor(disc_status.device, identity,
                               on_rebind=on_verified_rebind, cdtext=cdtext_reader)
                   if disc_status.device is not None else None)
        if monitor is not None:
            monitor.loaded_toc = toc  # verified startup TOC, never stale
            if toc is not None:
                monitor.start_cdtext(toc)
        ctrl.group_store = PlaybackGroupStore(ns.state_dir)
        serve(ctrl, ns.socket, power=power, drive_id=identity,
              drive_toc=toc, disc_monitor=monitor,
              target_store=TargetStore(ns.state_dir),
              metadata_worker=metadata_worker, drive_cdtext=startup_cdtext)
    except KeyboardInterrupt:
        print('\n[service] shutdown requested', flush=True)
    except Exception as exc:
        print(f'[service] service failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        if metadata_worker is not None:
            metadata_worker.close()
        if ctrl:
            result = ctrl.shutdown_owned(shutdown_policy)
            print(f'[shutdown] shutdown: {result}', flush=True)
            if result['action'] == 'not_stopped':
                print('[shutdown] WARNING: Sonos may retain a stream URI; no unverified STOP sent', flush=True)
            ctrl.close()
        if power is not None:
            power.close()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == '__main__':
    from sonoscdplayer.system import stack_dump
    from sonoscdplayer.system import version
    stack_dump.enable('player')
    print('[version] SonosCDPlayer ' + version.describe(), flush=True)
    sys.exit(run())
