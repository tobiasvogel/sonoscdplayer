# Shutdown and restart screens

While the player shuts down or restarts, the OLED shows what is happening instead of freezing on the last status screen or going dark:

| Moment | Screen (German / English) | Drawn by |
| --- | --- | --- |
| Services stop | "Wird heruntergefahren…" / "Shutting down…" | Display program, via the optional shutdown-screen service |
| Final stage of a restart | "Wird neu gestartet…" / "Restarting…" | Late-shutdown hook |
| Final stage of a power-off | "Bitte warten" / "Please wait" | Late-shutdown hook |
| Optional, just before power-off | "Kann jetzt ausgeschaltet werden" / "Safe to unplug now" | Experimental exitrd (see below) |

The start screen ("Wird gestartet…") belongs to the boot setup, see [Configuration](CONFIGURATION.md#boot-setup).

These screens are drawn by small C programs that run after the Python services have stopped. The screens for **every** locale file are built into them; at shutdown they pick the language chosen in the display menu, otherwise the one from `ui.toml` (see [Display languages](I18N.md)).

## How the hand-over works

1. On shutdown and restart the OLED service stops **before** the player, so it never reports a missing player.
2. The optional `sonoscdplayer-oled-shutdown-sentinel.service` tells the OLED service that the system is shutting down. The service then keeps its last picture and leaves the display switched on; on a normal service restart it still switches the display off. The C programs only write the picture and never switch the display on, so without the sentinel the later screens stay dark.
3. The optional `sonoscdplayer-shutdown-screen.service` draws "Wird heruntergefahren…" as soon as the services stop.
4. In the last stage, systemd runs the hook in `/usr/lib/systemd/system-shutdown/`, which draws the restart or power-off screen.

The screens are best effort: if the display cannot be reached, shutdown continues normally. None of them states that power may be removed, except the experimental screen below.

## Installation

The pieces come from the repository; run the commands from the repository root. Display pins are taken from `/etc/sonoscdplayer/player.toml`.

```sh
# Display program and late-shutdown hook (a small script that calls the display program)
sh tools/build_shutdown_renderer.sh
sudo install -m 0755 build/shutdown/sonoscdplayer-shutdown-display /usr/local/libexec/
sudo install -m 0755 systemd/sonoscdplayer-final-shutdown-hook \
     /usr/lib/systemd/system-shutdown/sonoscdplayer-final-screen

# Keep the last picture on shutdown, and show "Wird heruntergefahren…" early
sudo install -m 0644 systemd/sonoscdplayer-oled-shutdown-sentinel.service \
     systemd/sonoscdplayer-shutdown-screen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sonoscdplayer-oled-shutdown-sentinel.service sonoscdplayer-shutdown-screen.service
```

Instead of the hook script, a statically built hook (`sh tools/build_final_shutdown_hook.sh`, output in `build/shutdown-hook/`) can be installed under the same name; it carries its own copy of the screens.

## Updating

After an update of the texts, the artwork, the display pins or the locale files (for example a new language), rebuild everything that is installed in one step:

```sh
sh tools/install_screens.sh            # --dry-run shows what it would do
```

It updates only what is already installed: the display program, a statically built hook, and the experimental exitrd (asking for confirmation as on first install). It also refreshes the boot splash and the early pin settings in `config.txt`. Changing the language in the menu needs no rebuild of the shutdown screens.

## Optional: experimental "safe to unplug" screen (exitrd)

The experimental exitrd can show a final screen during the very last stage of shutdown, after the root filesystem has been unmounted.

### How it works

The shutdown integration runs after the normal system services have stopped. It unmounts the previous root filesystem, audits remaining mounts, and synchronises outstanding filesystem operations.

On the tested reference hardware, the optional implementation displays a final shutdown image, waits three seconds, then shows the final screen ("Kann jetzt ausgeschaltet werden" / "Safe to unplug now", in the menu language) immediately before requesting the final kernel power-off.

This late-shutdown display has been successfully tested on the Raspberry Pi reference setup, including persistent installation and automatic reactivation after a complete shutdown and power-on cycle (November 5, 2026).

**Note:** The final screen identifies the final shutdown stage, rather than independently verifying that electrical power-off has finished. The remaining sequence is normally very short on the tested hardware. For maximum assurance, you can also wait for the Raspberry Pi ACT LED's final shutdown sequence before disconnecting power.

The installer deliberately retains an explicit experimental-mode confirmation and conservative warnings. These safeguards remain in place while broader hardware validation is pending.

### Requirements

- A supported Raspberry Pi with the required shutdown integration.
- A compatible SSD1327 OLED and the required SPI/GPIO devices.

The integration is hardware-specific and is not automatically available on other display configurations.

### Installation and management

Run these commands from the repository root.

#### Check status

```sh
sh tools/manage_experimental_exitrd.sh --status
```

#### Enable persistent installation

```sh
sh tools/manage_experimental_exitrd.sh --install
```

The installer builds the shutdown component, enables `[service].experimental_safe_screen` in `/etc/sonoscdplayer/player.toml` (see [Configuration](CONFIGURATION.md)), and installs a systemd service that prepares the display integration at each startup.

Installation requires interactive confirmation.

#### Disable and uninstall

```sh
sh tools/manage_experimental_exitrd.sh --uninstall
```

This removes the persistent integration and disables its corresponding configuration option.

**These management commands do not themselves shut down or reboot the device.**

### Advanced: one-time activation

For a single supervised shutdown, the following commands are also available:

```sh
sh tools/manage_experimental_exitrd.sh --enable-once
sh tools/manage_experimental_exitrd.sh --disable
```

One-time activation requires `[service].experimental_safe_screen = true` in the selected player configuration and must be confirmed interactively. It is staged in RAM and does not automatically rearm after a subsequent startup. Use `--disable` to deactivate an active one-time stage before shutdown.

## Related documentation

- [Power button](POWER_BUTTON.md): requesting shutdown and restart. The screens do not depend on how the shutdown was started.
- [Display languages](I18N.md): languages and translations, including those of these screens.
- [Configuration](CONFIGURATION.md): configuration files, remembered state and boot setup.
