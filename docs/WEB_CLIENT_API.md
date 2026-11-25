# Local web API (experimental)

The SonosCDPlayer web service exposes a small JSON API on TCP port `8081`.
It is intended for the local network only, including community-built user
interfaces. Do **not** forward this port to the public Internet. The API is
experimental and may change before version 1.0.

The web service never controls Sonos or the optical drive directly: transport
commands go to the existing private player Unix socket. The physical player,
Sonos app, and the web API therefore share the same controller and ownership
checks. A request returning an uncertain result must not be retried blindly.

## Read-only resources

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Compact live player/disc/transport state; automatic polling is safe |
| `GET` | `/api/dashboard` | Full current-CD track titles, selected/playing speaker names, selection count; cached in the player |
| `GET` | `/api/targets` | Whether a target is selected and its speaker count (no UIDs) |
| `GET` | `/api/cover` | Cover of the inserted CD when available; no arbitrary filesystem paths |
| `GET` | `/api/theme` | Saved `auto`, `light` or `dark` preference |

The dashboard endpoint is read-only end to end: speaker names are discovered
asynchronously by the player, and all HTTP requests read an already published
snapshot. It does not perform CD or Sonos network I/O on demand. Unknown room
names remain unnamed; fallback speaker counts are shown instead.

Example (values are illustrative):

```json
{
  "ok": true,
  "track_count": 2,
  "tracks": [
    {"number": 1, "title": "First song", "artist": "Example artist"},
    {"number": 2, "title": null, "artist": null}
  ],
  "speaker_names": ["Music room"],
  "speaker_count": 1,
  "target_rev": 7,
  "playing_on_selection": true
}
```

`title` and `artist` are `null` when not known. The UI may show `Track N` as
a fallback. Speaker names are intentionally visible to LAN visitors, but
speaker identifiers, network addresses and private filesystem paths are not
included in public status/dashboard responses.

## Command API (retained for community clients)

`POST /api/command` accepts a JSON object with `Content-Type:
application/json`. An example payload is:

```json
{"command": "play-selected"}
```

Allowed simple commands at the current stage:

- `play-selected`, `pause`, `resume`, `stop`, `next`, `previous`, `eject`
- `volume +1`, `volume -1`, `reset-disc`
- `set-target` with a valid `uid` and optional `members` array (speaker UID
  discovery is not yet exposed through this API)

Success responds with `{"ok":true}`. Invalid input returns HTTP 400, a
refused command returns 409, and an unavailable or uncertain player response
returns 503. Do not automatically repeat side-effecting requests after 503.
The web service does not accept arbitrary player commands, force-STOP, or
recovery actions. Player-side ownership and group checks still apply.

`POST /api/theme` accepts `{"theme":"auto"}`, `light`, or `dark` and saves
this preference under the web state directory (`web.json`), not under `/etc`.

The bundled web page currently **does not provide transport buttons**. This
is intentional: it is a read-only music dashboard plus a separate management
area. The command API stays available to third-party integrations and a future
opt-in web player.

## Deployment and testing

The service runs with the same service account as the player because the
control socket is mode `0600`; merely sharing a Unix group is insufficient.
Keep the web unit independent of playback. When applying this change, update
both the player service and web service in the same maintenance window: older
player versions do not implement the `dashboard` request and the UI will
show track-number placeholders.

Run the web tests with:

```sh
PYTHONPATH=src python3 -m pytest tests/web -q
```