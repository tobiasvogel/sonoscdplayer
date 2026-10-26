#!/usr/bin/env python3
"""Send exactly one local command to M0.12a via an owner-only Unix socket."""
import argparse
import json
import socket
import sys


def request(path, command, *, uid=None, response_timeout=120):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)  # Local socket connection only
        client.connect(path)
        client.settimeout(response_timeout)  # Synchronous CD/Sonos operation
        payload = {'command': command}
        if uid is not None:
            payload['uid'] = uid
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
    parser.add_argument('--uid', help='Sonos target UID (required for PLAY)')
    parser.add_argument('command', choices=('status','play','next','previous','stop','select','help'))
    parser.add_argument('track', nargs='?', type=int)
    args = parser.parse_args(argv)
    if (args.command == 'select') != (args.track is not None):
        parser.error('select requires a track; other commands do not accept a track')
    if (args.command == 'play') != (args.uid is not None):
        parser.error('--uid is required for play and forbidden for other commands')
    if not 1 <= args.response_timeout <= 600:
        parser.error('--response-timeout must be between 1 and 600 seconds')
    command = f'select {args.track}' if args.track is not None else args.command
    try:
        result = request(args.socket, command, uid=args.uid, response_timeout=args.response_timeout)
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
