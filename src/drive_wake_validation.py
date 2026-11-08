"""Read-only drive verification after an explicitly requested motor wake.

Never authorizes Sonos playback on its own.
"""
from disc_startup import CDS_DISC_OK
import time



def _record(diagnostics, stage, **details):
    """Only record results of checks already being performed."""
    if diagnostics is not None:
        diagnostics.update(stage=stage, **details)


def verify_woken_disc(monitor, device, *, diagnostics=None):
    """Verify the original USB drive and CD, with optional passive trace."""
    if monitor is None or not isinstance(device, str):
        _record(diagnostics, "invalid_monitor_or_device")
        return False

    if monitor.device != device:
        _record(diagnostics, "device_path_changed")
        return False

    if monitor.identity is None or monitor.loaded_toc is None:
        _record(diagnostics, "original_identity_or_toc_missing")
        return False

    stage = "device_presence"
    try:
        if not monitor.path_exists(device):
            _record(diagnostics, "device_missing")
            return False

        stage = "usb_identity"
        if monitor.identity_reader(device) != monitor.identity:
            _record(diagnostics, "usb_identity_changed")
            return False

        stage = "media_status"
        media_code = monitor.media(device)
        if media_code != CDS_DISC_OK:
            _record(diagnostics, "media_not_ready",
                    media_code=media_code)
            return False

        stage = "toc_read"
        toc = monitor.read_toc(device)
        if not toc.tracks:
            _record(diagnostics, "toc_empty")
            return False

        stage = "device_recheck"
        if not monitor.path_exists(device):
            _record(diagnostics, "device_disappeared_during_toc")
            return False

        stage = "identity_recheck"
        if monitor.identity_reader(device) != monitor.identity:
            _record(diagnostics, "usb_identity_changed_during_toc")
            return False

        stage = "toc_comparison"
        if toc != monitor.loaded_toc:
            _record(diagnostics, "toc_changed",
                    observed_tracks=len(toc.tracks),
                    expected_tracks=len(monitor.loaded_toc.tracks))
            return False

        _record(diagnostics, "verified",
                observed_tracks=len(toc.tracks))
        return True

    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        _record(diagnostics, "check_exception",
                check=stage, exception=type(exc).__name__)
        return False



def wait_for_woken_disc(
    monitor,
    device,
    *,
    timeout_seconds=20.0,
    poll_seconds=0.5,
    clock=time.monotonic,
    sleep=time.sleep,
    diagnostics=None,
):
    """Wait for the original disc after one motor START.

    Repeats only read-only readiness checks, never the motor START.
    A changed or unavailable USB identity aborts immediately.
    """
    if (
        monitor is None
        or not isinstance(device, str)
        or monitor.device != device
        or monitor.identity is None
        or monitor.loaded_toc is None
        or not 0 < timeout_seconds <= 30
        or not 0 < poll_seconds <= 2
    ):
        _record(diagnostics, "invalid_wake_preconditions")
        return False

    deadline = clock() + timeout_seconds

    while True:
        if diagnostics is not None:
            diagnostics["attempts"] = diagnostics.get("attempts", 0) + 1

        stage = "outer_device_presence"
        try:
            if not monitor.path_exists(device):
                _record(diagnostics, "device_missing_before_check")
                return False

            stage = "outer_usb_identity"
            if monitor.identity_reader(device) != monitor.identity:
                _record(diagnostics, "usb_identity_changed_before_check")
                return False
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            _record(diagnostics, "check_exception",
                    check=stage, exception=type(exc).__name__)
            return False

        if verify_woken_disc(monitor, device, diagnostics=diagnostics):
            return True

        remaining = deadline - clock()
        if remaining <= 0:
            if diagnostics is not None:
                diagnostics["timeout_reached"] = True
            return False

        sleep(min(poll_seconds, remaining))
