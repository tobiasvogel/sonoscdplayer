"""Bounded single-attempt set-target request on the existing player socket.

Worker-only code. No GPIO, playback or Sonos calls. Never log UID.
"""
import json
import socket

DEFAULT_SOCKET = '/run/sonoscdplayer/control.sock'


def submit_target(uid, *, members=None, socket_path=DEFAULT_SOCKET, timeout=4.0):
    if not isinstance(uid, str) or not uid or len(uid)>128:
        raise ValueError('invalid target')
    request_data = {'command':'set-target','uid':uid}
    if members is not None:
        from sonos_group import normalize_members
        request_data['members'] = sorted(normalize_members(uid, members))
    request = json.dumps(request_data,separators=(',',':'))+'\n'
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(request.encode('utf-8'))
        response=bytearray()
        while b'\n' not in response and len(response)<=16384:
            chunk=sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    if b'\n' not in response or len(response)>16384:
        raise RuntimeError('invalid player response')
    reply=json.loads(response.split(b'\n',1)[0])
    if not isinstance(reply,dict) or reply.get('ok') is not True:
        raise RuntimeError('player rejected target')
    selection=reply.get('target_selection')
    if not isinstance(selection,dict) or selection.get('selected') is not True:
        raise RuntimeError('missing target acknowledgement')
    return True
