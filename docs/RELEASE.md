# Releases and updates

SonosCDPlayer has one version number in [`VERSION`](../VERSION), following
`MAJOR.MINOR.PATCH`: PATCH for fixes, MINOR for new functions or changed
behaviour, MAJOR once a change needs manual steps beyond the ones below.
Changes for users are listed in [`CHANGELOG.md`](../CHANGELOG.md).

Check which version runs:

```sh
(cd src && python3 -m sonoscdplayer.system.version)  # e.g. "SonosCDPlayer 0.9.0 (2b620d4)"
journalctl -u sonoscdplayer-player.service -b | grep '\[version\]'
```

The player status reply also carries it under `software` (`version`, and
`commit` in a git checkout; `-dirty` means local changes).

## Making a release

Run from the repository root on an up-to-date `main`.

1. **Tests pass**, on a development machine and on the device:
   `python3 -m pytest`
2. **Device test done** for everything listed under *Unreleased* in the
   changelog that touches playback, buttons, display or network.
3. **No private data**: no speaker names, Sonos UIDs, home paths, account
   names or passwords in the changes since the last release
   (`git diff v<last>..HEAD`; `python3 -m pytest -k local_paths` checks for home paths and account names).
4. **Version and changelog**: put the new number into `VERSION`, rename
   *Unreleased* in `CHANGELOG.md` to `## <number> – <date>` and start a new,
   empty *Unreleased* section. The test suite checks that the number in
   `VERSION` appears in the changelog.
5. **Commit and tag**:
   ```sh
   git commit -am "release: <number>"
   git tag -a v<number> -m "SonosCDPlayer <number>"
   git push origin main v<number>
   ```
   Optionally create a GitHub release from the tag with the changelog
   section as text.

## Updating the device

The services run from the checkout on the device, so an update is a git
update plus a restart. Do it while nothing is playing.

```sh
cd <repository>
OLD=$(git rev-parse --short HEAD)
git fetch --tags && git checkout v<number>      # or: git pull on main
python3 -m pytest
git diff --stat $OLD..HEAD -- systemd tools/render_runtime_units.py \
    src/native/apds9306 config
```

The last command shows what needs more than a restart:

| Changed | Then |
|---|---|
| `systemd/` or `tools/render_runtime_units.py` | Render the units again and install them (see [Configuration](CONFIGURATION.md)) |
| `src/native/apds9306/…` | Rebuild the sensor service: `make service && sudo make install` in its `service/` directory |
| `config/*.example.toml` | Compare with `/etc/sonoscdplayer/*.toml`; new options have defaults, so this is only needed to use them |

Then restart the services and check the version:

```sh
sudo systemctl restart sonoscdplayer-player sonoscdplayer-led sonoscdplayer-oled sonoscdplayer-buttons
(cd src && python3 -m sonoscdplayer.system.version)
```

**Back to the previous version:** `git checkout <previous tag or $OLD>`,
undo the extra steps from the table if any were needed, restart the
services.
