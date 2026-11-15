import unittest
from src.integrations.outputs.oled_status import StatusHold

class TestBusyStatusHold(unittest.TestCase):
    def test_busy_grace_keeps_last_known_state(self):
        h = StatusHold(4)
        state = {'player': {'mode': 'STOPPED'}}
        h.success(state, 100)
        self.assertIs(h.failure(115, 50), state)
        self.assertIsNone(h.failure(151, 50))
    def test_normal_disconnection_remains_short(self):
        h = StatusHold(4)
        h.success({'player': {'mode': 'STOPPED'}}, 100)
        self.assertIsNone(h.failure(105))
    def test_never_claim_prior_status_without_snapshot(self):
        self.assertIsNone(StatusHold(4).failure(101, 50))
