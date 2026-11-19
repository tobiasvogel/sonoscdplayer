# Setup orchestrator (initial implementation)

`bash scripts/setup.sh` runs an explicit questionnaire for a Raspberry Pi OS Lite
installation. Every operational decision must be answered; no yes/no default is
chosen. Use `--dry-run` for an overview without changes or `--apply` to proceed.
The mode itself is also asked when omitted. `--color auto|always|never` controls
ANSI colours (auto requires an interactive terminal and respects `NO_COLOR`).

## Interactive suggestions

Interactive prompts display suggested values to help first-time installers.
Press **Enter** to explicitly accept the displayed suggestion, or type another
value. `[Y/n]` suggests yes; `[y/N]` suggests no for hardware-specific or
potentially disruptive options (such as starting services immediately).
The service account suggestion is the existing account invoking `sudo`, or
`pi` if that account exists. If neither can be found, an account name must be
entered. The suggested, root-managed venv path is
`/opt/sonoscdplayer/.venv`. Suggestions are not inferred for command-line
automation: omitted flags still fail without an interactive terminal.
The `--apply` confirmation remains mandatory and separate from these prompts.

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
- `tools/build_shutdown_renderer.sh` to compile the C shutdown renderer,
  followed by installation of the renderer and final shutdown hook, and
  `tools/install_screens.sh` to update the boot configuration and existing components.

Existing different service files or `.service.d/*.conf` drop-ins require manual
review and migration; they are not automatically overwritten or removed.
The optional early-power-LED helper is copied only when explicitly selected.
The experimental exitrd is **not** installed automatically; use the documented
`tools/manage_experimental_exitrd.sh` procedure separately. When `--screens yes` is selected, setup separately asks whether to prepare the
firmware splash assembler. With `--splash-assembler yes`,
`tools/prepare_splash_assembler.sh` fetches a **pinned** official Raspberry Pi
utils commit into `build/` (and verifies the existing checkout on later runs).
No upstream `master` checkout is silently used for this step. The installer
passes the exact `splash_assembler.py` path to `tools/install_screens.sh`.
This needs Git and network access on first use; `--splash-assembler no` keeps
the existing lookup behaviour and may skip the firmware splash.

With `--screens yes --services yes`, the additional `--shutdown-services yes`
choice installs the optional OLED sentinel and early shutdown systemd units
through `tools/install_runtime_services.py`. This is an explicit opt-in,
separate from building the C renderer and installing the final shutdown hook.
Existing differing units or drop-ins are still refused. The renderer and hook
are built and installed before any selected services are started.
Selecting the APDS9306 sensor also requires selecting systemd service installation.

Before running `--start-services`, verify your actual GPIO wiring and display,
configuration, Sonos target and intended ownership. Setup does not start a CD
stream or select speakers by itself. Always test the whole setup on a fresh
Raspberry Pi before claiming a reproducible beginner installation.

## Preflight and explicit installation confirmation

`tools/setup_preflight.py` is invoked after all choices are collected and
**before** `--apply` can change the system. It validates the Raspberry Pi OS
platform, required commands, package templates, existing TOML files, expected
source files, existing service conflicts and drop-ins (by rendering units into
a temporary directory), and availability of the selected screen components.
The preflight does not install packages, modify configuration, or call systemctl.
The `--dry-run` mode runs the same checks, but hardware/OS mismatches are
warnings so the plan can be reviewed on a development computer.

In interactive `--apply`, after successful preflight the operator must type
`APPLY` exactly to confirm the displayed choices. For unattended execution,
provide `--confirm-apply` explicitly with `--apply`; omitting this flag fails
before any system changes. This confirmation authorizes selected operations,
not a reboot or an OS upgrade. Do not use `--confirm-apply` with `--dry-run`.

Preflight cannot prove correct GPIO wiring, Sonos availability or that a
Raspberry Pi firmware accepts the requested splash format. A full Pi acceptance
test remains necessary before describing the installer as production-ready.
