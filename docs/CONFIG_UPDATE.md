# Updating the player configuration

`tools/config_swap.py` replaces the production config the simple way: stop the
service, swap the file, start the service. It never writes anything unless you
pass `--apply`.

```sh
# 1. Prepare the new file anywhere (for example a copy of the old one with your edits).
# 2. Dry run: validates the file and checks the player is idle.
python3 tools/config_swap.py \
    --config /home/pi/sonoscdplayer/player.toml \
    --new ~/player.toml.new \
    --reference config/player.example.toml

# 3. Really swap (needs permission to run systemctl, so usually sudo).
sudo python3 tools/config_swap.py --config /home/pi/sonoscdplayer/player.toml \
    --new ~/player.toml.new --reference config/player.example.toml --apply
```

What it does with `--apply`:

1. Rejects invalid TOML. With `--reference`, it also rejects a key whose type
   differs from the reference; missing keys are only listed (the code has its
   own defaults) and unknown keys are listed as "not in reference".
2. Asks the running player for its cached status and **refuses while a CD is
   playing, paused or starting, or while a Sonos session is still owned**. If
   no player answers on the control socket, nothing can be interrupted and it
   continues. `--force` swaps anyway and interrupts playback.
3. Stops the service, keeps the old file as `player.toml.bak-<timestamp>`
   (mode 0600), and replaces the config atomically. Owner and mode of the old
   file are kept, so a swap done as root stays readable by the `pi` service.
4. Starts the service and waits a few seconds. If it is not active, the old
   file is restored and the service is started again.

Only key names are printed, never values. By default the unit
`sonoscdplayer-player.service` is restarted; pass `--service` (repeatable) to
include others, such as the buttons unit.

If you only changed the file and nothing else, you can of course still do it
by hand: `sudo systemctl stop …`, copy the file, `sudo systemctl start …`.
