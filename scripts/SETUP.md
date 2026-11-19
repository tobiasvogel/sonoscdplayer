# Setup orchestrator (initial implementation)

`bash scripts/setup.sh` runs an explicit questionnaire for a Raspberry Pi OS Lite
installation. Every operational decision must be answered; no yes/no default is
chosen. Use `--dry-run` for an overview without changes or `--apply` to proceed.
The mode itself is also asked when omitted. `--color auto|always|never` controls
ANSI colours (auto requires an interactive terminal and respects `NO_COLOR`).

Run from any directory within a local checkout. Example for an unattended **preview**:

```sh
bash scripts/setup.sh --dry-run --service-user <account> --venv <absolute-venv-path> \
    --packages yes --spi yes --i2c yes --config yes --sensor yes \
    --services yes --early-led yes --power-actions yes \
    --drive-activity-events yes --connect-gestures yes --network-setup yes \
    --enable-services no --start-services no --screens no --color never
```

The example is not a universal recommendation. On real hardware use the
interactive questions to match the installed components. A real install needs
root (`sudo bash scripts/setup.sh --apply`). The service account must already
exist and be a non-root account; the installer does not invent user names,
hostnames, secrets, network addresses or Python environment paths. It neither
runs `apt full-upgrade` nor reboots. The installed config defaults to
`/etc/sonoscdplayer` and existing TOMLs are retained.

The setup calls the existing project helpers:

- `tools/install_config.py` for creating missing configuration files.
- `tools/render_runtime_units.py` for rendering services and Polkit rules.
- `tools/install_runtime_services.py` for reviewing and installing rendered
  units/rules (without implicit service start or enable).
- The APDS9306 `service/Makefile` (`make service`) to build both C programs;
  binaries are installed separately, not with `make install` (which would
  implicitly enable and start the sensor service).
- `tools/build_shutdown_renderer.sh` to compile the C shutdown renderer, and
  `tools/install_screens.sh` to update boot settings and previously installed
  shutdown components.

Existing different service files or `.service.d/*.conf` drop-ins require manual
review and migration; they are not automatically overwritten or removed.
The optional early-power-LED helper is copied only when explicitly selected.
The experimental exitrd is **not** installed automatically; use the documented
`tools/manage_experimental_exitrd.sh` procedure separately. The firmware
splash assembler from Raspberry Pi utils is likewise not cloned or built
implicitly. `install_screens.sh` reports when it is unavailable. A new
shutdown screen service/hook installation is not yet automated by this first
setup revision; the screen installer refreshes components already installed.

Before running `--start-services`, verify your actual GPIO wiring and display,
configuration, Sonos target and intended ownership. Setup does not start a CD
stream or select speakers by itself. Always test the whole setup on a fresh
Raspberry Pi before claiming a reproducible beginner installation.
