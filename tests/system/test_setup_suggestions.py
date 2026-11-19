"""Interactive suggestions: pressing Enter is a conscious local selection only."""
import os
import pty
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


def prompt_functions():
    script = (ROOT / 'scripts/setup.sh').read_text(encoding='utf-8')
    return script.split('ask_yes_no() {', 1)[1].split("\ninfo 'SonosCDPlayer setup", 1)[0]


def run_prompt(command, answer=None):
    script = (
        "set -euo pipefail\n"
        "warn() { printf 'WARN:%s\\n' \"$*\" >&2; }\n"
        "fail() { printf 'FAIL:%s\\n' \"$*\" >&2; exit 1; }\n"
        + 'ask_yes_no() {' + prompt_functions() + '\n'
        + command + '\n'
    )
    if answer is None:
        return subprocess.run(['bash', '-c', script], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(['bash', '-c', script], stdin=slave,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(slave)
        slave = -1
        os.write(master, answer.encode('utf-8'))
        out, err = proc.communicate(timeout=5)
        return proc.returncode, out.decode(), err.decode()
    finally:
        os.close(master)
        if slave != -1:
            os.close(slave)


class SetupSuggestionsTests(unittest.TestCase):
    def test_yes_suggestion_by_enter(self):
        rc, out, err = run_prompt("CHOICE=''; ask_yes_no CHOICE 'Install packages?'; echo \"$CHOICE\"", '\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), 'yes')
        self.assertIn('[Y/n]', err)

    def test_no_suggestion_by_enter(self):
        rc, out, err = run_prompt("CHOICE=''; ask_yes_no CHOICE 'Start now?' no; echo \"$CHOICE\"", '\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), 'no')
        self.assertIn('[y/N]', err)

    def test_explicit_no_overrides_yes_suggestion(self):
        rc, out, err = run_prompt("CHOICE=''; ask_yes_no CHOICE 'Install packages?'; echo \"$CHOICE\"", 'n\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), 'no')

    def test_text_suggestion_by_enter(self):
        rc, out, err = run_prompt("VALUE=''; ask_text VALUE 'Venv' /opt/sonoscdplayer/.venv; echo \"$VALUE\"", '\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), '/opt/sonoscdplayer/.venv')
        self.assertIn('[/opt/sonoscdplayer/.venv]', err)

    def test_text_override(self):
        rc, out, err = run_prompt("VALUE=''; ask_text VALUE 'Venv' /opt/sonoscdplayer/.venv; echo \"$VALUE\"", '/tmp/custom-venv\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), '/tmp/custom-venv')

    def test_no_terminal_does_not_accept_suggestion(self):
        result = run_prompt("CHOICE=''; ask_yes_no CHOICE 'Install packages?'")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing --choice selection', result.stderr)

    def test_cli_choice_overrides_suggestion(self):
        result = run_prompt("CHOICE='no'; ask_yes_no CHOICE 'Install packages?'; echo \"$CHOICE\"")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'no')


if __name__ == '__main__':
    unittest.main()
