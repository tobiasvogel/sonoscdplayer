import unittest
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.integrations.interfaces.command_bridge import Bridge, Command, map_event, send_command

UID = 'RINCON_TEST_123'

class BridgeTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(map_event('playpause', 'pressed', UID), Command('playpause', UID))
        self.assertEqual(map_event('skip', 'pressed'), Command('next'))
        self.assertEqual(map_event('previous', 'pressed'), Command('previous'))
        self.assertEqual(map_event('stop', 'pressed'), Command('stop'))
    def test_release_ignored(self):
        self.assertIsNone(map_event('stop', 'released'))
        self.assertIsNone(map_event('encoder', 'right'))
        self.assertIsNone(map_event('power', 'pressed'))
    def test_play_requires_uid(self):
        with self.assertRaises(ValueError): map_event('playpause', 'pressed')
    def test_dry_run_never_sends(self):
        sender=MagicMock()
        b=Bridge(uid=UID, sender=sender)
        self.assertEqual(b.event('playpause','pressed')['command'],'playpause')
        sender.assert_not_called()
    def test_execute_exactly_once(self):
        sender=MagicMock(return_value={'ok':True})
        b=Bridge(uid=UID, sender=sender, dry_run=False)
        self.assertEqual(b.event('skip','pressed'),{'ok':True})
        self.assertEqual(sender.call_count, 1)
        sender.assert_called_once_with('/tmp/sonoscdplayer-control.sock', Command('next'))
    def test_no_force_flag(self):
        self.assertEqual(map_event('stop','pressed').payload(),{'command':'stop'})
    def test_sender_timeout_propagates_without_retry(self):
        with patch('src.integrations.interfaces.command_bridge.socket.socket') as factory:
            client=factory.return_value.__enter__.return_value
            client.recv.side_effect=TimeoutError('timed out')
            with self.assertRaises(TimeoutError):
                send_command('/tmp/nonexistent', Command('next'))
            self.assertEqual(client.sendall.call_count,1)
    def test_invalid_uid(self):
        with self.assertRaises(ValueError): Bridge(uid='bad uid')

if __name__ == '__main__': unittest.main()
