#!/bin/sh
# Explicit one-boot opt-in management; does not run poweroff or install services.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
ACTION=${1:---help}
CONFIG=${2:-}

usage() {
    cat <<'EOF'
Usage:
  sh tools/manage_experimental_exitrd.sh --status --config /path/to/player.toml
  sh tools/manage_experimental_exitrd.sh --enable-once --config /path/to/player.toml
  sh tools/manage_experimental_exitrd.sh --disable

Before --enable-once set [service] experimental_safe_screen = true in your
real player TOML. This script builds the experiment, verifies its mode and
SHA-256, and stages it for ONLY THIS BOOT after an interactive acknowledgement.
It never shuts down, enables a systemd service or arranges re-arming at boot.
WARNING: The OLED safe graphic appears BEFORE kernel poweroff. Do not unplug
until the Raspberry Pi ACT LED has completed its final shutdown sequence.
EOF
}

if [ "$ACTION" = "--help" ] || [ "$ACTION" = "-h" ]; then
    usage
    exit 0
fi

case "$ACTION" in
    --disable)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        if ! mountpoint -q /run/initramfs; then
            echo 'No staged one-boot exitrd detected. Nothing to deactivate.'
            exit 0
        fi
        sudo "$PYTHON" "$ROOT/tools/exitrd_once.py" --deactivate
        exit
        ;;
    --status|--enable-once)
        [ "$#" -eq 3 ] && [ "$2" = "--config" ] && [ -n "$3" ] || {
            usage >&2; exit 2;
        }
        CONFIG=$3
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

"$PYTHON" "$ROOT/tools/exitrd_player_config.py" --config "$CONFIG"
if mountpoint -q /run/initramfs; then
    echo "RAM exitrd mount: staged (inspect with sudo /run/initramfs/shutdown --check)"
else
    echo "RAM exitrd mount: inactive"
fi

if [ "$ACTION" = "--status" ]; then
    exit 0
fi

# Config and host preflight fail before any build/activation. No existing RAM
# exitrd may be overwritten, even if its ownership cannot be determined.
if mountpoint -q /run/initramfs; then
    echo "Refusing: /run/initramfs is already mounted; use --disable only if ours" >&2
    exit 1
fi

"$PYTHON" - "$CONFIG" "$ROOT/tools" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from exitrd_player_config import enabled_from_player_config
if not enabled_from_player_config(Path(sys.argv[1])):
    raise SystemExit("Refusing: enable [service].experimental_safe_screen = true in the actual player TOML first")
PY

[ -t 0 ] || { echo 'Refusing: interactive terminal required for risk confirmation' >&2; exit 1; }
cat >&2 <<'WARNING'

WARNING — EXPERIMENTAL MODE, AT YOUR OWN RISK:
The "Safe to unplug" artwork is displayed BEFORE the final kernel poweroff.
The 3-second delay is NOT evidence of completed shutdown or storage durability.
Wait until the completed ACT LED shutdown sequence BEFORE unplugging power.
The exitrd can prevent a clean shutdown if it fails. Staging is RAM-only and
applies solely to the next shutdown; no persistent install is performed.
WARNING
printf '%s' 'Type YES-I-ACCEPT to build and stage the experimental exitrd: ' >&2
IFS= read -r REPLY
[ "$REPLY" = 'YES-I-ACCEPT' ] || { echo 'Cancelled; nothing changed.' >&2; exit 1; }

# Build is kept separate from elevated staging. In particular, do not allow
# caller-provided build variant flags to select another binary.
(
    unset BUILD_DIR EXITRD_DIAGNOSTIC
    EXITRD_UNVERIFIED_SAFE=1 \
    EXITRD_UNVERIFIED_SAFE_ACK=I_ACCEPT_EARLY_SAFE_SCREEN_RISK \
       sh "$ROOT/tools/build_exitrd.sh"
)

BIN="$ROOT/build/exitrd-unverified-safe/shutdown"
"$BIN" --check
HASH=$(sha256sum "$BIN" | awk '{print $1}')
echo "Expected experimental SHA-256: $HASH"

# The underlying helper re-checks actual binary mode, host status, arch, hash,
# mount safety and the two explicit acknowledgements before touching /run.
sudo "$PYTHON" "$ROOT/tools/exitrd_player_config.py" \
    --config "$CONFIG" \
    --activate-once --expected-sha256 "$HASH" \
    --acknowledge I_UNDERSTAND_EXITRD_CAN_PREVENT_CLEAN_SHUTDOWN \
    --acknowledge-risk I_ACCEPT_EARLY_SAFE_SCREEN_RISK

echo 'READY FOR ONE SUPERVISED SHUTDOWN. No poweroff was triggered.'
echo 'To undo before shutdown: sh tools/manage_experimental_exitrd.sh --disable'
