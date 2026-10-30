"""Safe GPIO-to-player command mapping. No socket access or Sonos dependencies."""
from __future__ import annotations

# Unsupported hardware controls are intentionally unmapped.
PRESS_COMMANDS = {
    'previous': 'previous',
    'stop': 'stop',
    'playpause': 'playpause',
    'skip': 'next',
}


def map_event(name: str, event: str) -> str | None:
    """Only one command per physical press; never dispatch releases or encoder motion."""
    if event != 'pressed':
        return None
    return PRESS_COMMANDS.get(name)
