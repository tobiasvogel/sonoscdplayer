# Displays

The player uses one display at a time. SSD1327 and SSD1351 are connected to
the live player, including menus and the boot/shutdown screen builders. Choose
the controller through `[display]` in `player.toml`; see
[Display configuration](DISPLAY_CONFIGURATION.md). Other entries remain
standalone diagnostics or evaluated hardware as noted below.

| Module | Controller | Size | Interface | State |
|---|---|---|---|---|
| OLED, 128×128 grayscale | SSD1327 | 128×128 | SPI | In use by the player |
| Waveshare 1.5" RGB OLED Module, SKU 14747 | SSD1351 | 128×128, color | SPI | Live player, menu, notices and colored startup/shutdown logo confirmed on hardware. See [SSD1351](SSD1351.md) |
| Waveshare 1.3" OLED Module (C), SKU 18179 | SH1107 | 128×64 (landscape) | SPI, I2C | Driver and diagnostic; SPI confirmed on hardware, I2C pending. See [SH1107](SH1107.md) |
| Waveshare 1.8" LCD Module, SKU 13892 | ST7735S | 160×128 (landscape), color | SPI | Driver and diagnostic confirmed on hardware (RGB, rotation 270). See [ST7735S](ST7735S.md) |
| Waveshare 1.54" e-Paper Module (G), SKU 30475 | – | 200×200, four colors | SPI | **Evaluated, not suitable** |

## Evaluated and not suitable

**Waveshare 1.54" four-color e-Paper Module (G), SKU 30475.** The module works,
but it has no partial refresh and a full image takes about 20 seconds. That is
too slow for track and status changes and for navigating the menus. No driver
for it is included; its geometry is kept as a reference profile
(`REFERENCE_PROFILES` in `sonoscdplayer/ui/displays/profiles.py`) so that it is never
offered as a player display.

## Choosing pins

All SPI displays share SPI0 (MOSI BCM10, SCLK BCM11, CE0 BCM8). DC and RESET
are free GPIOs; the project wiring uses BCM22 and BCM27. Never run two
renderers on the same display or pins: stop `sonoscdplayer-oled.service`
before testing another module.
