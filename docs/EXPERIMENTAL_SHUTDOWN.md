# Optional OLED shutdown screen

SonosCDPlayer can display a **"Safe to unplug"** message on a supported OLED during the final stage of shutdown.

The message provides a visual indication that the system has completed the main shutdown steps and is about to request the final kernel power-off.

## How it works

The shutdown integration runs after the normal system services have stopped. It unmounts the previous root filesystem, audits remaining mounts, and synchronises outstanding filesystem operations.

On the tested reference hardware, the optional implementation displays a final shutdown image, waits three seconds, then shows **"Safe to unplug"** immediately before requesting the final kernel power-off.

This late-shutdown display has been successfully tested on the Raspberry Pi reference setup, including persistent installation and automatic reactivation after a complete shutdown and power-on cycle (November 5, 2026).

**Note:** The message identifies the final shutdown stage, rather than independently verifying that electrical power-off has finished. The remaining sequence is normally very short on the tested hardware. For maximum assurance, you can also wait for the Raspberry Pi ACT LED's final shutdown sequence before disconnecting power.

The installer deliberately retains an explicit experimental-mode confirmation and conservative warnings. These safeguards remain in place while broader hardware validation is pending.

## Requirements

- A supported Raspberry Pi with the required shutdown integration.
- A compatible SSD1327 OLED and the required SPI/GPIO devices.

The integration is hardware-specific and is not automatically available on other display configurations.

## Installation and management

Run these commands from the repository root.

### Check status

```sh
sh tools/manage_experimental_exitrd.sh --status
```

### Enable persistent installation

```sh
sh tools/manage_experimental_exitrd.sh --install
```

The installer builds the shutdown component, enables `[service].experimental_safe_screen` in `/etc/sonoscdplayer/player.toml` (see [Configuration](CONFIGURATION.md)), and installs a systemd service that prepares the display integration at each startup.

Installation requires interactive confirmation.

### Disable and uninstall

```sh
sh tools/manage_experimental_exitrd.sh --uninstall
```

This removes the persistent integration and disables its corresponding configuration option.

**These management commands do not themselves shut down or reboot the device.**

## Advanced: One-time activation

For a single supervised shutdown, the following commands are also available:

```sh
sh tools/manage_experimental_exitrd.sh --enable-once
sh tools/manage_experimental_exitrd.sh --disable
```

One-time activation requires `[service].experimental_safe_screen = true` in the selected player configuration and must be confirmed interactively. It is staged in RAM and does not automatically rearm after a subsequent startup. Use `--disable` to deactivate an active one-time stage before shutdown.

## Related documentation

The [Power button](POWER_BUTTON.md) is a separate, optional control for requesting shutdown and restart. The OLED shutdown screen does not depend on how the normal shutdown was initiated.
