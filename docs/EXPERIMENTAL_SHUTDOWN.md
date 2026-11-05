# Experimental OLED Shutdown Screen

Optional feature for SonosCDPlayer on a supported Raspberry Pi. It displays an experimental "Safe to unplug" graphic during shutdown.

**Warning:** Experimental feature — use at your own risk. The graphic appears **before** the final kernel poweroff. **Do not disconnect power until the ACT LED has completed its shutdown blinking sequence.** The three-second delay is not a safety guarantee.

From the project directory:

```sh
# Check status
sh tools/manage_experimental_exitrd.sh --status

# Install persistently (interactive confirmation required)
sh tools/manage_experimental_exitrd.sh --install

# Disable and uninstall
sh tools/manage_experimental_exitrd.sh --uninstall
```

Installation builds the required binary, enables the option in `src/config.toml`, and installs a systemd service that prepares the feature at each startup. Uninstallation removes the service and disables the option. **These commands do not shut down or reboot the Raspberry Pi.**

For one shutdown only, `--enable-once` and `--disable` remain available. One-time activation requires `[service].experimental_safe_screen = true` in `src/config.toml`.

**Requirements:** Supported Raspberry Pi with an SSD1327 OLED and the required SPI/GPIO devices.

**Hardware verified (November 5, 2026):** Persistent installation and automatic re-arming after a full shutdown and power-on cycle were successfully tested.
