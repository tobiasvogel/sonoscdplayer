import tempfile
import unittest
from pathlib import Path
from src.integrations.outputs.oled_status import StatusHold, request_status
from drive_power import load_config

class M014BR1Tests(unittest.TestCase):
    def test_retain_recent_status(self):
        hold=StatusHold(4)
        value={'player':{'mode':'PLAYING'}}
        self.assertIs(hold.success(value,10),value)
        self.assertIs(hold.failure(13.99),value)
        self.assertIsNone(hold.failure(14))
    def test_offline_without_prior_success(self):
        self.assertIsNone(StatusHold().failure(100))
    def test_refresh_success_resets_grace(self):
        hold=StatusHold(4)
        hold.success({'player':{'mode':'PLAYING'}},1)
        hold.success({'player':{'mode':'PAUSED'}},5)
        self.assertEqual(hold.failure(8)['player']['mode'],'PAUSED')
    def test_zero_grace_is_immediate(self):
        hold=StatusHold(0)
        hold.success({},2)
        self.assertIsNone(hold.failure(2))
    def test_config_default_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'config.toml'; f.write_text('[service]\nshutdown_policy="stop_owned"\n')
            self.assertTrue(load_config(f)['enabled'])
    def test_explicit_disabled_respected(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'config.toml'; f.write_text('[drive_power]\nenabled=false\n')
            self.assertFalse(load_config(f)['enabled'])
    def test_bundled_config_enabled(self):
        self.assertTrue(load_config(Path(__file__).resolve().parents[1]/'config.toml')['enabled'])
