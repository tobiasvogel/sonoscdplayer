"""Commands that waited behind a slow one (audit RPC-001)."""
import socket

from sonoscdplayer.player.service import client_gone, run_if_still_wanted


def test_command_of_a_client_that_gave_up_is_not_run():
    server, client = socket.socketpair()
    client.close()                                   # timed out and left
    ran = []
    reply = run_if_still_wanted(lambda r: ran.append(r) or {'ok': True}, server, {'command': 'play'})
    assert ran == [] and reply['ok'] is False
    server.close()


def test_command_of_a_waiting_client_runs():
    server, client = socket.socketpair()
    server.settimeout(2.0)
    assert not client_gone(server)
    assert server.gettimeout() == 2.0                # timeout restored
    assert run_if_still_wanted(lambda r: {'ok': True}, server, {'command': 'play'}) == {'ok': True}
    server.close(); client.close()
