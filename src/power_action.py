"""The one place that powers the Raspberry Pi off or reboots it.

Both the button process (long press) and the OLED process (power menu) call
:class:`PowerRequester`. It runs a fixed ``systemctl`` command line, never a
shell and never anything derived from input. The processes run as the
unprivileged service user, so the permission comes from the narrow polkit rule
in ``systemd/polkit/50-sonoscdplayer-power.rules`` (see docs/POWER_BUTTON.md).
systemd then stops the units in order; the player service stops a CD stream
and releases Sonos on SIGTERM.
"""
from __future__ import annotations

import subprocess
import threading

SYSTEMCTL = '/usr/bin/systemctl'
COMMANDS = {
    'poweroff': (SYSTEMCTL, '--no-ask-password', 'poweroff'),
    'reboot': (SYSTEMCTL, '--no-ask-password', 'reboot'),
}


class PowerRequester:
    """Issue a poweroff/reboot at most once; a failed attempt may be retried."""

    def __init__(self, *, run=subprocess.run, timeout=15.0, log=print):
        self._run = run
        self._timeout = timeout
        self._log = log
        self._lock = threading.Lock()
        self._issued = None

    @property
    def issued(self):
        return self._issued

    def request(self, action):
        """Return True when systemd accepted ``action``; never raises for a failed command."""
        if action not in COMMANDS:
            raise ValueError('unknown power action')
        with self._lock:
            if self._issued is not None:
                self._log(f'power {action} ignored: {self._issued} already requested', flush=True)
                return False
            try:
                result = self._run(list(COMMANDS[action]), capture_output=True, text=True,
                                   timeout=self._timeout, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                self._log(f'power {action} failed: {type(exc).__name__}', flush=True)
                return False
            if result.returncode != 0:
                detail = (result.stderr or '').strip().splitlines()[:1]
                self._log(f'power {action} refused (exit {result.returncode}): '
                          f'{detail[0][:120] if detail else "no message"}', flush=True)
                return False
            self._issued = action
        self._log(f'power {action} requested', flush=True)
        return True
