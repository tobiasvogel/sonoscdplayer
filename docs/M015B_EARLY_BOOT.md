# M0.15b — Experimental early-boot OLED and Power LED

This is **opt-in**. It does not modify Sonos playback, CD streaming, buttons or audio buffering.

## Source image

Use the existing repository artwork: `src/data/start_screen.png`. No example PNG or example splash source is included.

Install Pillow (`python3-pil`) and obtain Raspberry Pi's official `splashasm` assembler separately. The upstream `splashasm` tool is available from `raspberrypi/utils/splashasm`; the official instructions currently require Python 3.12 or newer. Firmware execution of SPI/I2C splash programs is a beta feature and must be confirmed on the actual bootloader/firmware before installation. On a Pi with Python 3.12 or later:

```sh
python3 tools/build_ssd1327_splash.py src/data/start_screen.png -o /tmp/ssd1327.splash
python3 splash_assembler.py /tmp/ssd1327.splash -o /tmp/ssd1327.bin
```

Inspect the generated source and verify the firmware supports `splash_screen` **before** touching the boot partition. The experimental generator uses SSD1327 SPI0/CE0, DC25, GPIO10/11, 8192 bytes in high-nibble-first order and explicit `0x5c` WRITE_RAM.

## Firmware stage — opt-in, back up first

Back up `/boot/firmware/config.txt`. Check that GPIO24 RESET is reliably HIGH *before* firmware starts SPI transactions; firmware GPIO defaults alone do not prove that the OLED has received a valid reset. An explicit firmware-stage high level (for example `gpio=24=op,dh` where supported) is only a candidate to test, **not** evidence of a valid hardware reset pulse. Do not add it blindly: confirm the board's reset circuit and actual boot behavior first. On appropriate supported firmware, install the assembled `ssd1327.bin` to `/boot/firmware/` and add:

```ini
gpio=12=op,dh
gpio=13=op,dl
# Only if hardware RESET timing has been verified:
splash_screen=ssd1327.bin
```

These settings assume active-HIGH LEDs. A blank/unstable display at firmware phase is a **stop condition**: revert the firmware setting and use the existing Python initialization.

## Early Linux Power LED — opt-in

The early helper controls **GPIO12 only**. It never reads Sonos, the sensor, or the optical drive. The systemd unit expects the helper installed at `/opt/sonoscdplayer-boot/early_power_led.py`.

```sh
sudo install -d -m 0755 /opt/sonoscdplayer-boot
sudo install -m 0755 tools/early_power_led.py /opt/sonoscdplayer-boot/early_power_led.py
sudo install -m 0644 systemd/sonoscdplayer-early-power-led.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sonoscdplayer-early-power-led.service
```

Do **not** start the early LED service while the regular LED controller owns BCM12. The example drop-in in `systemd/sonoscdplayer-frontend.service.d-example.conf` is for adaptation to the **actual** frontend service name. Establish and verify an explicit systemd conflict and ordering so that the early service exits and cleans up GPIO12 **before** normal LED PWM takes over. Do not enable the early service until this is configured and checked using `systemctl cat` and `systemd-analyze verify`. A short handoff flicker is permissible.

## OLED adoption — opt-in

After firmware initialization is physically verified, start the normal frontends with:

```sh
PYTHONPATH="$PWD:$PWD/src" python3 src/frontends.py --hardware --adopt-boot-splash --buttons --commands --uid '<SONOS_UID>'
```

(Use your existing local configuration and launch environment.) The flag propagates to OLED status only. `SSD1327.open(adopt_existing=True)` configures GPIO/SPI but **does not reset or initialize the OLED** and avoids redrawing the splash PNG; the existing readiness wait then displays the first valid status frame or the service-offline fallback. **Do not enable adoption if the firmware splash failed.** Without this flag, all existing initialization and splash behavior remains the default.

The current `close()` still sends `0xAE` and blanks the screen on exit. It is not an uninterrupted shutdown sequence.

## Rollback

1. Disable the experimental early LED unit: `sudo systemctl disable --now sonoscdplayer-early-power-led.service`.
2. Remove any experimental frontend unit drop-in and run `sudo systemctl daemon-reload`.
3. Remove `--adopt-boot-splash` from the application launch options.
4. Restore the backed-up `/boot/firmware/config.txt` (remove `splash_screen` and experimental GPIO settings), then reboot.
5. The normal SSD1327 driver initializes and displays the PNG itself as before.

## Validation checklist

- [ ] Python syntax and all repository tests (`PYTHONPATH="$PWD:$PWD/src" python3 -m unittest discover -s src/tests -q`)
- [ ] Validate generated `.splash` with the official splash assembler, including the complete 8192-byte data payload
- [ ] Firmware OLED image persists and GPIO24 RESET is valid
- [ ] OLED adoption leaves the firmware image intact until first valid player status
- [ ] Power LED steady during firmware, breathes during early Linux; connection LED remains OFF
- [ ] No GPIO12 overlap during handoff; normal adaptive brightness works afterwards
- [ ] Restart/shutdown/failure-recovery and rollback on Raspberry Pi
- [ ] Existing Sonos, playback and CD-drive hardware regression

**Status:** experimental implementation. The assembler grammar and DATA_ONLY flag are described in the upstream `raspberrypi/utils/splashasm/README.md`, but successful compilation and execution of this project's generated source have not yet been verified on the target Pi. The firmware currently requires beta access according to Raspberry Pi's official announcement; Raspberry Pi firmware and integrated hardware validation pending. Do not merge until validated.
