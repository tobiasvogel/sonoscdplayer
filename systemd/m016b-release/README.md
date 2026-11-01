# M0.16b finalization candidate

This directory contains reviewed *candidates*, not changes to the existing
installed units. Keep current functioning m016b-candidate drop-ins until the
production Python environment has been verified.

## First: verify production venv, without touching services

```bash
/home/pi/sonoscdplayer/.venv/bin/python -c "import soco, PIL, RPi.GPIO, spidev; print('Production dependencies OK')"
cd /home/pi/src/sonoscdplayer/src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

If either command fails, stop. Do not replace the working test-venv drop-ins.

## Review and stage

```bash
systemctl cat sonoscdplayer-oled.service sonoscdplayer-buttons.service
systemctl show -p User,ExecStart,WorkingDirectory sonoscdplayer-oled.service sonoscdplayer-buttons.service
```

Only while no CD is playing, backup the installed m016b-ui.conf files and
replace them with the matching files from this directory. Do not overwrite
other site-specific drop-ins. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl stop sonoscdplayer-buttons.service sonoscdplayer-oled.service
sudo systemctl start sonoscdplayer-oled.service
sudo systemctl start sonoscdplayer-buttons.service
systemctl status --no-pager sonoscdplayer-oled.service sonoscdplayer-buttons.service
```

Validate OLED menu, encoder and ambient contrast. Check boot behaviour only
after manual validation. For rollback, restore the previously backed-up
m016b-ui.conf files, daemon-reload and restart only the two frontend services.
Do not restart or change the player service.

All checkbox selections are local-only and NOT applied to Sonos.
The Buttons service remains command-preview only, without --execute.
No speaker grouping changes and no multi-target transport commands.
