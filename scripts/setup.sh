#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Interactive, explicit first-install orchestration; does not upgrade the OS.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Same override as the player (src/sonoscdplayer/system/config_paths.py).
CONFIG_DIR="${SONOSCDPLAYER_CONFIG_DIR:-/etc/sonoscdplayer}"
PACKAGES="$ROOT/scripts/packages.lst"

usage() {
  cat <<'EOF'
Usage: bash scripts/setup.sh [--apply|--dry-run] [--service-user NAME] [--venv PATH]
  [--packages yes|no] [--spi yes|no] [--i2c yes|no]
  [--config yes|no] [--sensor yes|no] [--services yes|no] [--early-led yes|no]
  [--power-actions yes|no] [--drive-activity-events yes|no]
  [--connect-gestures yes|no] [--network-setup yes|no]
  [--screens yes|no] [--shutdown-services yes|no] [--splash-assembler yes|no]
  [--enable-services yes|no] [--start-services yes|no] [--confirm-apply]
  [--color auto|always|never]

--dry-run prints a plan and makes no system changes.
Missing choices are asked interactively; without a terminal they are errors.
--apply always requires a final typed confirmation or --confirm-apply.
Existing configuration and different service files are never overwritten.
The setup does not change hostname, run full-upgrade, or reboot.
EOF
}

APPLY='' COLOR=auto CONFIRM_APPLY=no
SERVICE_USER='' VENV=''
PACKAGES_OPT='' SPI='' I2C='' CONFIG='' SENSOR='' SERVICES='' EARLY='' POWER='' WAKE='' CONNECT='' NETWORK='' SCREENS='' SHUTDOWN='' ASSEMBLER='' ENABLE='' START=''
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --confirm-apply) CONFIRM_APPLY=yes; shift ;;
    --apply) [[ -z "$APPLY" ]] || { echo "Choose exactly one mode" >&2; exit 2; }; APPLY=yes; shift ;;
    --dry-run) [[ -z "$APPLY" ]] || { echo "Choose exactly one mode" >&2; exit 2; }; APPLY=no; shift ;;
    --service-user) SERVICE_USER="${2:?Missing service user}"; shift 2 ;;
    --venv) VENV="${2:?Missing venv path}"; shift 2 ;;
    --packages) PACKAGES_OPT="${2:?}"; shift 2 ;;
    --spi) SPI="${2:?}"; shift 2 ;;
    --i2c) I2C="${2:?}"; shift 2 ;;
    --config) CONFIG="${2:?}"; shift 2 ;;
    --sensor) SENSOR="${2:?}"; shift 2 ;;
    --services) SERVICES="${2:?}"; shift 2 ;;
    --early-led) EARLY="${2:?}"; shift 2 ;;
    --power-actions) POWER="${2:?}"; shift 2 ;;
    --drive-activity-events) WAKE="${2:?}"; shift 2 ;;
    --connect-gestures) CONNECT="${2:?}"; shift 2 ;;
    --network-setup) NETWORK="${2:?}"; shift 2 ;;
    --screens) SCREENS="${2:?}"; shift 2 ;;
    --shutdown-services) SHUTDOWN="${2:?}"; shift 2 ;;
    --splash-assembler) ASSEMBLER="${2:?}"; shift 2 ;;
    --enable-services) ENABLE="${2:?}"; shift 2 ;;
    --start-services) START="${2:?}"; shift 2 ;;
    --color) COLOR="${2:?}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$COLOR" in auto|always|never) ;; *) echo 'Invalid --color' >&2; exit 2 ;; esac
if [[ "$COLOR" == always || ( "$COLOR" == auto && -t 1 && -z "${NO_COLOR+x}" && "${TERM:-dumb}" != dumb ) ]]; then
  BLUE=$'\033[1;34m' GREEN=$'\033[1;32m' YELLOW=$'\033[1;33m' RED=$'\033[1;31m' RESET=$'\033[0m'
else BLUE='' GREEN='' YELLOW='' RED='' RESET=''; fi
info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"; }
ok() { printf '%s[ OK ]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
fail() { printf '%s[FAIL]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

# Suggestions apply only to interactive answers. CLI arguments are never inferred.
# Pressing Enter explicitly accepts the displayed suggestion.
ask_yes_no() {
  local name="$1" question="$2" value="${!1}" suggested="${3:-yes}" answer choice
  case "$suggested" in
    yes) choice='Y/n' ;;
    no) choice='y/N' ;;
    *) fail "Invalid suggested answer for $question: $suggested" ;;
  esac
  if [[ -z "$value" ]]; then
    [[ -t 0 ]] || fail "Missing --${name,,} selection for non-interactive setup: $question"
    while true; do
      read -r -p "$question [$choice]: " answer || fail 'Input closed'
      case "$answer" in
        '') value="$suggested"; break ;;
        y|Y|yes|YES) value=yes; break ;;
        n|N|no|NO) value=no; break ;;
        *) warn 'Please answer y or n, or press Enter to accept the suggestion.' ;;
      esac
    done
  fi
  case "$value" in yes|no) ;; *) fail "Invalid value for $question: $value (expected yes or no)" ;; esac
  printf -v "$name" '%s' "$value"
}
ask_text() {
  local name="$1" question="$2" value="${!1}" suggested="${3:-}" answer
  if [[ -z "$value" ]]; then
    [[ -t 0 ]] || fail "Missing required value: $question"
    if [[ "$name" == VENV ]]; then
      # Readline enables Tab completion; -i inserts the editable path proposal.
      read -r -e -i "$suggested" -p "$question: " answer || fail 'Input closed'
    elif [[ -n "$suggested" ]]; then
      read -r -p "$question [$suggested]: " answer || fail 'Input closed'
    else
      read -r -p "$question: " answer || fail 'Input closed'
    fi
    value="${answer:-$suggested}"
  fi
  [[ -n "$value" ]] || fail "Empty value: $question"
  printf -v "$name" '%s' "$value"
}

info 'SonosCDPlayer setup — decisions require explicit answers.'
if [[ -z "$APPLY" ]]; then
  [[ -t 0 ]] || fail 'Specify --apply or --dry-run in non-interactive mode'
  while true; do
    read -r -p 'Choose mode: [p]review or [i]nstall: ' mode || fail 'Input closed'
    case "$mode" in p|P) APPLY=no; break;; i|I) APPLY=yes; break;; *) warn 'Choose p or i (no default).';; esac
  done
fi
# A real install also needs root privileges to inspect protected Polkit files.
# Fail before questions or preflight instead of reporting a false unit conflict.
if [[ "$APPLY" == yes && $EUID -ne 0 ]]; then
  fail 'Installation requires root privileges: sudo bash scripts/setup.sh --apply'
fi
# Raspberry Pi OS does not guarantee that an account named 'pi' exists.
# Prefer the account invoking sudo; fall back to 'pi' only if it exists.
suggested_user=''
if [[ "${SUDO_USER:-}" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "${SUDO_USER:-}" != root ]] && getent passwd "$SUDO_USER" >/dev/null; then
  suggested_user="$SUDO_USER"
elif getent passwd pi >/dev/null; then
  suggested_user=pi
fi
ask_text SERVICE_USER 'Existing non-root service account' "$suggested_user"
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$SERVICE_USER" != root ]] || fail 'Invalid service account name'
getent passwd "$SERVICE_USER" >/dev/null || fail 'Service account does not exist; create it explicitly before setup'
ask_text VENV 'Absolute path for the Python virtual environment' '/opt/sonoscdplayer/.venv'
[[ "$VENV" == /* && "$VENV" != *[[:space:]]* ]] || fail 'Virtual environment path must be absolute and contain no whitespace'
ask_yes_no PACKAGES_OPT 'Install operating-system packages?'
ask_yes_no SPI 'Enable SPI interface?'
ask_yes_no I2C 'Enable I2C interface?'
ask_yes_no CONFIG 'Create missing configuration files?'
ask_yes_no SENSOR 'Build/install the APDS9306 C sensor service?' no
ask_yes_no SERVICES 'Render and install systemd services and Polkit rules?'
if [[ "$SERVICES" == yes ]]; then
  ask_yes_no EARLY 'Install early-boot Power LED service? (SSD1327 reference wiring)' no
  ask_yes_no POWER 'Enable POWER actions in rendered services?' no
  ask_yes_no WAKE 'Enable optical-drive wake from buttons?'
  ask_yes_no CONNECT 'Enable CONNECT gestures?'
  ask_yes_no NETWORK 'Enable WLAN setup?' no
  ask_yes_no ENABLE 'Enable installed services at boot?'
  ask_yes_no START 'Start services now?' no
else
  for unwanted in EARLY POWER WAKE CONNECT NETWORK ENABLE START; do
    [[ -z "${!unwanted}" ]] || fail "--services no conflicts with option $unwanted"
  done
  EARLY=no POWER=no WAKE=no CONNECT=no NETWORK=no ENABLE=no START=no
fi
if [[ "$SENSOR" == yes && "$SERVICES" != yes ]]; then
  fail 'APDS9306 sensor service requires --services yes; no binaries have been installed.'
fi
ask_yes_no SCREENS 'Install SSD1327 boot settings, C renderer and final shutdown hook?' no
if [[ "$SCREENS" == yes ]]; then
  ask_yes_no ASSEMBLER 'Prepare pinned Raspberry Pi splash assembler from upstream Git?'
  if [[ "$SERVICES" == yes ]]; then
    ask_yes_no SHUTDOWN 'Install optional OLED shutdown sentinel and early shutdown screen services?' no
  else
    [[ -z "$SHUTDOWN" ]] || fail '--shutdown-services conflicts with --services no'
    SHUTDOWN=no
    warn 'Optional shutdown services require --services yes.'
  fi
else
  [[ -z "$ASSEMBLER" && -z "$SHUTDOWN" ]] || fail 'Splash assembler and shutdown services require --screens yes'
  ASSEMBLER=no SHUTDOWN=no
fi

info "Repository: $ROOT"
info "Service account: $SERVICE_USER; Python venv: $VENV"
info "Packages=$PACKAGES_OPT SPI=$SPI I2C=$I2C Config=$CONFIG Sensor=$SENSOR Services=$SERVICES Screens=$SCREENS"
info "EarlyLED=$EARLY Power=$POWER Wake=$WAKE Connect=$CONNECT Network=$NETWORK Enable=$ENABLE Start=$START"
info "SplashAssembler=$ASSEMBLER ShutdownServices=$SHUTDOWN"
# The preflight is read-only. It also checks rendered units against installed files.
preflight=(python3 "$ROOT/tools/setup_preflight.py" --repo "$ROOT"
  --service-user "$SERVICE_USER" --venv "$VENV"
  --mode "$( [[ "$APPLY" == yes ]] && printf apply || printf preview )"
  --packages "$PACKAGES_OPT" --spi "$SPI" --i2c "$I2C"
  --config "$CONFIG" --sensor "$SENSOR" --services "$SERVICES"
  --screens "$SCREENS" --assembler "$ASSEMBLER"
  --early-led "$EARLY" --shutdown-services "$SHUTDOWN"
  --power-actions "$POWER" --drive-activity-events "$WAKE"
  --connect-gestures "$CONNECT" --network-setup "$NETWORK")
if [[ "$APPLY" == no && "$CONFIRM_APPLY" == yes ]]; then
  fail '--confirm-apply requires --apply'
fi
info 'Running read-only preflight checks'
"${preflight[@]}" || fail 'Preflight failed; no installation steps were started.'
if [[ "$APPLY" == no ]]; then
  ok 'Preview only; no system modifications performed. Rerun with --apply to install.'
  exit 0
fi
[[ $EUID -eq 0 ]] || fail 'Use sudo for --apply (only after reviewing the plan).'
if [[ "$CONFIRM_APPLY" != yes ]]; then
  [[ -t 0 ]] || fail 'Non-interactive --apply requires --confirm-apply'
  answer=''
  read -r -p 'Type APPLY to confirm all selected system changes: ' answer || fail 'Input closed'
  [[ "$answer" == APPLY ]] || fail 'Installation not confirmed; nothing changed.'
fi
command -v python3 >/dev/null || fail 'python3 is required'
if [[ "$SPI" == yes || "$I2C" == yes ]]; then
  command -v raspi-config >/dev/null || fail 'raspi-config is required to configure SPI or I2C'
fi

if [[ "$PACKAGES_OPT" == yes ]]; then
  mapfile -t pkgs < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$PACKAGES")
  info 'Installing OS packages'; apt-get update; apt-get install -y "${pkgs[@]}"
fi
if [[ "$SPI" == yes ]]; then info 'Enabling SPI'; raspi-config nonint do_spi 0; fi
if [[ "$I2C" == yes ]]; then info 'Enabling I2C'; raspi-config nonint do_i2c 0; fi

if [[ ! -x "$VENV/bin/python" ]]; then
  info 'Creating Python environment'; python3 -m venv --system-site-packages "$VENV"
fi
info 'Installing Python runtime dependencies'
"$VENV/bin/python" -m pip install soco Pillow

if [[ "$CONFIG" == yes ]]; then
  info 'Creating only missing configuration files'
  python3 "$ROOT/tools/install_config.py" --service-user "$SERVICE_USER" --apply
fi
if [[ "$SENSOR" == yes ]]; then
  SENSOR_DIR="$ROOT/src/native/apds9306/service"
  info 'Compiling APDS9306 sensor helpers'
  make -C "$SENSOR_DIR" service
  install -m 0755 "$SENSOR_DIR/apds9306d" /usr/local/bin/apds9306d
  install -m 0755 "$SENSOR_DIR/apds9306" /usr/local/bin/apds9306
fi
if [[ "$SCREENS" == yes ]]; then
  [[ -f "$CONFIG_DIR/player.toml" ]] || fail 'Screens require the installed player.toml'
  if [[ "$ASSEMBLER" == yes ]]; then
    info 'Preparing pinned Raspberry Pi splash assembler'
    SPLASH_ASSEMBLER="$(bash "$ROOT/tools/prepare_splash_assembler.sh")"
    export SPLASH_ASSEMBLER
  fi
  info 'Building C shutdown display helper'
  sh "$ROOT/tools/build_shutdown_renderer.sh"
  info 'Installing C shutdown renderer and final shutdown hook'
  install -d -m 0755 /usr/local/libexec /usr/lib/systemd/system-shutdown
  install -m 0755 "$ROOT/build/shutdown/sonoscdplayer-shutdown-display" /usr/local/libexec/sonoscdplayer-shutdown-display
  install -m 0755 "$ROOT/systemd/sonoscdplayer-final-shutdown-hook" /usr/lib/systemd/system-shutdown/sonoscdplayer-final-screen
  info 'Updating configured boot and existing shutdown screens'
  sh "$ROOT/tools/install_screens.sh"
fi
if [[ "$SERVICES" == yes ]]; then
  [[ -f "$CONFIG_DIR/player.toml" ]] || fail 'Missing player.toml; create configuration before service installation'
  [[ -f "$CONFIG_DIR/interface.toml" ]] || fail 'Missing interface.toml; create configuration before service installation'
  [[ -x "$VENV/bin/python" ]] || fail 'Missing Python venv'
  out="$(mktemp -d)"
  trap 'rm -rf -- "${out:-}"' EXIT
  render=(python3 "$ROOT/tools/render_runtime_units.py" --output-dir "$out" --checkout "$ROOT/src" --python-venv "$VENV/bin/python" --service-user "$SERVICE_USER")
  for pair in "$POWER:power-actions" "$WAKE:drive-activity-events" "$CONNECT:connect-gestures" "$NETWORK:network-setup"; do
    value="${pair%%:*}"; name="${pair#*:}"
    [[ "$value" == yes ]] && render+=("--$name") || render+=("--no-$name")
  done
  info 'Rendering service files and rules'; "${render[@]}"
  install_cmd=(python3 "$ROOT/tools/install_runtime_services.py" --rendered-dir "$out" --target-unit "$ROOT/systemd/sonoscdplayer.target" --apply)
  [[ "$SENSOR" == yes ]] && install_cmd+=(--sensor-unit "$SENSOR_DIR/apds9306d.service")
  [[ "$SHUTDOWN" == yes ]] && install_cmd+=(--shutdown-units-dir "$ROOT/systemd")
  [[ "$ENABLE" == yes ]] && install_cmd+=(--enable)
  [[ "$START" == yes ]] && install_cmd+=(--start)
  if [[ "$EARLY" == yes ]]; then
    info 'Installing early Power LED helper'
    install -d -m 0755 /opt/sonoscdplayer-boot
    install -m 0755 "$ROOT/tools/early_power_led.py" /opt/sonoscdplayer-boot/early_power_led.py
    install_cmd+=(--early-led)
  fi
  info 'Installing reviewed service files'; "${install_cmd[@]}"
fi
ok 'Setup steps completed. No reboot initiated.'
