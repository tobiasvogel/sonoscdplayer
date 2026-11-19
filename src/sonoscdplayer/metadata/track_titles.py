"""Conservative track-number prefix cleanup shared by CD-TEXT and MusicBrainz.

Titles are changed only when every present title in the medium agrees with
its track position and at least two titles provide independent evidence.
"""
from __future__ import annotations

import re

_NUMBERED_TITLE = re.compile(r'^\s*(\d{1,2})(?:[.)][ \t]*|[ \t]+-[ \t]+|[ \t]+)(\S.*)$')


def without_redundant_track_numbers(titles):
    """Remove redundant prefixes in an unambiguous numbered track sequence.

    A non-matching numeric start, an isolated numbered title or an unnumbered
    title prevents cleanup. The input is never modified.
    """
    numbered = []
    for position, title in enumerate(titles, 1):
        if not title:
            continue
        match = _NUMBERED_TITLE.fullmatch(title)
        if match is None or int(match.group(1)) != position:
            return tuple(titles)
        numbered.append((position - 1, match.group(2)))
    if len(numbered) < 2:
        return tuple(titles)
    result = list(titles)
    for index, title in numbered:
        result[index] = title
    return tuple(result)
