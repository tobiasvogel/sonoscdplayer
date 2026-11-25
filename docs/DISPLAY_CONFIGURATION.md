# Display selection from configuration

The live service, firmware splash builder and native shutdown builders use
the same `[display]` table in `player.toml`. Runtime backends currently supported:
**SSD1327** (128x128 grayscale) and **SSD1351** (128x128 RGB565). Other catalogued
displays remain diagnostic-only. No automatic hardware detection is attempted.

For the tested Waveshare SSD1351 module and enclosure wiring:

```toml
[display]
controller = "ssd1351"
interface = "spi"
spi_bus = 0
spi_device = 0
dc_pin = 22
rst_pin = 27
rotation = 0
color_order = "rgb"
spi_hz = 2000000
contrast = 128
ambient_auto = true
adopt_boot_splash = false
preserve_on_exit = false
recover_on_error = true
```

Keep the actual wiring values when switching controller. GPIO9 is unused by
both OLEDs. Power off and disconnect power before replacing hardware. The
software choice itself is `controller = "ssd1327"` or `"ssd1351"`; no controller
flags, Python edits, backend environment variables or different systemd units
are needed. Both use `sonoscdplayer-oled.service`, including real player status,
encoder/menu events, target RPC, volume, language and Wi-Fi setup.

The existing layout is shared. Color is preserved until the output adapter;
the grayscale backend keeps its established packed-frame output. Rotation and
RGB/BGR selection are implemented for SSD1351. The SSD1327 backend currently
requires rotation 0 and the default color order. Unsupported combinations fail
before hardware is opened. Both currently require SPI0/CE0.

## Apply the configuration

After updating the branch and changing the installed `player.toml`, preview:

```sh
python3 tools/apply_display_config.py
```

Then apply the same configuration to installed display artifacts and restart
the frontend:

```sh
python3 tools/apply_display_config.py --apply
```

`--config /path/to/player.toml` is available for a nonstandard config location.
The installed service must read that same path. This command validates first,
stops only the frontend, refreshes firmware/installed shutdown programs through
the common screen installer, then restarts the same frontend. It never changes
the TOML, requests power-off, or reboots. A failed refresh stops the apply before
the frontend restart; fix the error and apply again. The physical module must
already match the configuration. Do not apply a different controller to a
connected module just to inspect its UI.

Firmware and late-shutdown executables cannot read TOML themselves. They are
generated from it when applying changes; the early firmware splash takes effect
on the next boot. Do not merely restart the frontend while retaining compiled
artifacts for a different controller. Missing splash assembly tooling with an
existing boot program is an error, rather than permission to reuse stale bytes.
The stable managed boot filename is now `display.bin`; the boot-config tool
migrates the project's former `splash_screen=ssd1327.bin` setting.

The experimental exitrd keeps its existing installation/confirmation workflow
when already installed. It is not automatically enabled by changing a display.
Its builder now selects the backend from the same TOML, without a separate
`BACKEND` choice. The ordinary shutdown hook/display builders do likewise.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `controller` | `ssd1327` | Registered runtime backend |
| `interface` | `spi` | Validated interface |
| `spi_bus`, `spi_device` | `0`, `0` | SPI0/CE0 |
| `dc_pin`, `rst_pin` | `25`, `24` | BCM control pins; enclosure uses 22/27 |
| `rotation` | `0` | SSD1351: 0/90/180/270 |
| `color_order` | `rgb` | SSD1351: rgb/bgr |
| `spi_hz` | `2000000` | 100 kHz..8 MHz |
| `contrast` | `128` | Common initial brightness scale, 0..255 |
| `ambient_auto` | `false` | Existing APDS9306 brightness control |
| `adopt_boot_splash` | `false` | Adopt matching firmware initialization/RAM |
| `preserve_on_exit` | `false` | Preserve frame during coordinated handover |
| `recover_on_error` | `false` | One reinitialization after a reported SPI error |

For SSD1351, 0..255 maps to C7 levels 0..15. This gives sixteen brightness
steps; zero is minimum current, not display-off. Ambient control uses the same
mapping and suppresses duplicate C7 writes within a level. Display-off remains
a distinct command. The driver can preserve/adopt existing RAM without reset.
Only set `adopt_boot_splash=true` after generating matching firmware assets.
The shutdown sentinel can still request preservation during system shutdown.

## Existing installations

This refactor removes display-behavior flags from both the versioned frontend
unit and `render_runtime_units.py`. Install the updated generic unit once using
the project's normal unit deployment workflow. Older local units/drop-ins may
still explicitly pass `--ambient-auto`, `--adopt-boot-splash`, `--contrast` or
`--oled-recover-on-error`; those diagnostic/compatibility overrides continue to
win over configuration until removed during this migration. Subsequent display
changes use only `[display]` and the common apply command.

The standalone diagnostic still accepts its own explicit GPIO and test-pattern
arguments. It does not change the runtime configuration.

Hardware status: SSD1351 diagnostics and RGB overview/menu previews were verified
on the project module. The new live-service binding, firmware splash and native
shutdown presentation still need end-to-end verification on the Pi. In particular,
do not infer panel operation from successful write-only SPI transfers. Small
status-font readability is a separate follow-up for both 128x128 OLEDs.

## Software validation

Validated with Python 3.13: 350 UI tests, 165 hardware/driver tests and 84
configuration/deployment tests passed, plus an additional rotation/boot-name
migration test. The UI set includes the new live-service and native-C mock
protocol tests. One native transport build test was skipped on macOS. Tests
requiring Unix socket binding, unavailable pytest modules or Linux system
facilities were excluded; this is not a claim that the entire Linux suite ran.
Shell syntax checks for all four modified build/install scripts also passed.
