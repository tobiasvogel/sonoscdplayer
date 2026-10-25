#!/usr/bin/env python3
"""Read-only disc inventory and offline track selection demo; never controls Sonos."""
import argparse
import json
from cd_toc import read_toc, TOCError
from player_state import PlayerState


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', required=True)
    p.add_argument('--track', type=int, default=1)
    p.add_argument('--next', action='store_true', dest='go_next')
    p.add_argument('--previous', action='store_true')
    a = p.parse_args()
    if a.go_next and a.previous:
        p.error('--next and --previous cannot be used together')
    try:
        disc = read_toc(a.device)
        state = PlayerState().disc_loaded(tuple(t.number for t in disc.tracks)).select(a.track)
        if a.go_next: state = state.next_track()
        if a.previous: state = state.previous_track()
    except (TOCError, ValueError) as exc:
        p.error(str(exc))
    track = disc.get_track(state.selected_track)
    print(json.dumps({'mode': state.mode.value, 'device': a.device,
                      'track_count': len(disc.tracks), 'selected_track': state.selected_track,
                      'duration_seconds': round(track.duration_seconds, 3),
                      'pcm_bytes': track.pcm_bytes}, indent=2))


if __name__ == '__main__':
    main()
