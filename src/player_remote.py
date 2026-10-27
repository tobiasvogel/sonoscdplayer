#!/usr/bin/env python3
"""Send exactly one local command to M0.12a via an owner-only Unix socket."""
import argparse
import json
import socket
import sys


def request(path, command, *, uid=None, force=False, response_timeout=120):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)  # Local socket connection only
        client.connect(path)
        client.settimeout(response_timeout)  # Synchronous CD/Sonos operation
        payload = {'command': command}
        if uid is not None:
            payload['uid'] = uid
        if force:
            payload['force'] = True
        client.sendall((json.dumps(payload) + '\n').encode())
        data = bytearray()
        while b'\n' not in data and len(data) < 8192:
            chunk = client.recv(2048)
            if not chunk:
                break
            data.extend(chunk)
        if b'\n' not in data:
            raise RuntimeError('incomplete service reply')
        return json.loads(data.split(b'\n', 1)[0])


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default='/tmp/sonoscdplayer-control.sock')
    parser.add_argument('--response-timeout', type=float, default=120,
                        help='seconds to wait for a command response (default: 120)')
    parser.add_argument('--uid', help='Sonos target UID (PLAY, live status or forced STOP)')
    parser.add_argument('--force', action='store_true', help='emergency STOP of explicitly selected solo speaker; bypasses ownership')
    parser.add_argument('command', choices=('status','sonos-status','play','playpause','pause','resume','next','previous','stop','select','reset','help'))
    parser.add_argument('track', nargs='?', type=int)
    args = parser.parse_args(argv)
    if (args.command == 'select') != (args.track is not None):
        parser.error('select requires a track; other commands do not accept a track')
    if args.force and args.command != 'stop':
        parser.error('--force is only accepted with stop')
    if args.command in ('play', 'playpause', 'sonos-status') or (args.command == 'stop' and args.force):
        if not args.uid:
            parser.error('PLAY, sonos-status and forced STOP require --uid')
    elif args.uid is not None:
        parser.error('--uid is only accepted with PLAY, sonos-status or STOP --force')
    if not 1 <= args.response_timeout <= 600:
        parser.error('--response-timeout must be between 1 and 600 seconds')
    command = f'select {args.track}' if args.track is not None else args.command
    try:
        result = request(args.socket, command, uid=args.uid, force=args.force, response_timeout=args.response_timeout)
    except socket.timeout:
        print('No response before timeout; command outcome UNKNOWN. Do NOT blindly repeat '+
              'PLAY/NEXT/PREVIOUS/STOP. Check: python3 player_remote.py status', file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Local service unavailable: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get('ok') else 1

if __name__ == '__main__':
    sys.exit(run())
