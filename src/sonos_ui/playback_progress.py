"""Pure parsing of Sonos-reported playback timing; never estimate progress.

This module performs no network access. Unknown/unreliable fields remain None.
"""
import re

_CLOCK = re.compile(r'^(\d{1,3}):([0-5]\d):([0-5]\d)$')


def parse_position(value):
    """Accept Sonos H:MM:SS; reject missing, malformed or unbounded values."""
    if not isinstance(value, str):
        return None
    match = _CLOCK.fullmatch(value)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if total <= 359999 else None


def verified_progress(info, owned_uri):
    """Return a bounded position/duration only for this exact owned URI."""
    if not isinstance(info, dict) or not owned_uri or info.get('uri') != owned_uri:
        return None
    elapsed = parse_position(info.get('position'))
    duration = parse_position(info.get('duration'))
    if elapsed is None:
        return None
    # Sonos commonly reports 0:00:00 for unknown durations. We must not
    # present it as a known track length or extrapolate the position.
    if duration == 0:
        duration = None
    if duration is not None and elapsed > duration:
        return None
    return {'elapsed_seconds': elapsed, 'duration_seconds': duration}
