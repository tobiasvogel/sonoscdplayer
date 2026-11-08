"""Read-only drive verification after an explicitly requested motor wake.

Never authorizes Sonos playback on its own.
"""
from disc_startup import CDS_DISC_OK
import time


def verify_woken_disc(monitor, device):
    """Require the same USB drive and the previously verified audio CD.

    Called only by the serialized player worker after motor START.
    """
    if monitor is None or not isinstance(device, str):
        return False

    if monitor.device != device:
        return False

    if monitor.identity is None or monitor.loaded_toc is None:
        return False

    try:
        if not monitor.path_exists(device):
            return False

        if monitor.identity_reader(device) != monitor.identity:
            return False

        if monitor.media(device) != CDS_DISC_OK:
            return False

        toc = monitor.read_toc(device)
        if not toc.tracks:
            return False

        # Recheck USB identity after potentially blocking TOC access.
        # Never authorize a disc read across an observed reconnect.
        if not monitor.path_exists(device):
            return False
        if monitor.identity_reader(device) != monitor.identity:
            return False

        return toc == monitor.loaded_toc

    except (OSError, RuntimeError, ValueError, TimeoutError):
        return False



def wait_for_woken_disc(
    monitor,
    device,
    *,
    timeout_seconds=20.0,
    poll_seconds=0.5,
    clock=time.monotonic,
    sleep=time.sleep,
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
        return False

    deadline = clock() + timeout_seconds

    while True:
        try:
            if not monitor.path_exists(device):
                return False

            if monitor.identity_reader(device) != monitor.identity:
                return False
        except (OSError, RuntimeError, ValueError, TimeoutError):
            return False

        if verify_woken_disc(monitor, device):
            return True

        remaining = deadline - clock()
        if remaining <= 0:
            return False

        sleep(min(poll_seconds, remaining))
