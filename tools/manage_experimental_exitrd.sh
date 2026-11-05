#!/bin/sh
# Guided experimental exitrd management; no poweroff/reboot issued.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
ACTION=${1:---help}
CONFIG="$ROOT/src/config.toml"

usage() {
    cat <<'EOF'
Usage:
  sh tools/manage_experimental_exitrd.sh --status [--config /path/to/player.toml]
  sh tools/manage_experimental_exitrd.sh --enable-once [--config /path/to/player.toml]
  sh tools/manage_experimental_exitrd.sh --install\n  sh tools/manage_experimental_exitrd.sh --uninstall\n  sh tools/manage_experimental_exitrd.sh --disable

By default the script uses src/config.toml (the player service config).
The --install option sets [service].experimental_safe_screen = true in src/config.toml,\nbuilds a verified binary, and installs a root-owned boot service. It will re-arm\nat each boot until --uninstall disables it. --enable-once remains RAM-only.\nThe script never initiates shutdown or reboot.
WARNING: The OLED safe graphic appears BEFORE kernel poweroff. Do not unplug
until the Raspberry Pi ACT LED has completed its final shutdown sequence.
EOF
}

if [ "$ACTION" = "--help" ] || [ "$ACTION" = "-h" ]; then
    usage
    exit 0
fi

case "$ACTION" in
    --uninstall)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        sudo "$PYTHON" "$ROOT/tools/install_exitrd_persistent.py" --uninstall
        exit
        ;;
    --install)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        [ -t 0 ] || { echo 'Interactive terminal required' >&2; exit 1; }
        cat >&2 <<'WARNING'

WARNING: EXPERIMENTAL PERSISTENT INSTALLATION, AT YOUR OWN RISK.
The "Safe to unplug" graphic appears BEFORE the kernel's final poweroff.
The 3-second delay is NOT evidence of a completed safe shutdown.
NEVER unplug before the ACT LED's shutdown blink sequence has fully ended.
The boot service will re-arm the experimental exitrd after EACH startup.
Unexpected exitrd errors may prevent a clean shutdown.
WARNING
        printf '%s' 'Type YES-I-ACCEPT to install permanently: ' >&2
        IFS= read -r REPLY
        [ "$REPLY" = 'YES-I-ACCEPT' ] || { echo 'Cancelled.' >&2; exit 1; }
        (
            unset BUILD_DIR EXITRD_DIAGNOSTIC
            EXITRD_UNVERIFIED_SAFE=1 \
            EXITRD_UNVERIFIED_SAFE_ACK=I_ACCEPT_EARLY_SAFE_SCREEN_RISK \
                sh "$ROOT/tools/build_exitrd.sh"
        )
        BIN="$ROOT/build/exitrd-unverified-safe/shutdown"
        HASH=$(sha256sum "$BIN" | awk '{print $1}')
        sudo "$PYTHON" "$ROOT/tools/install_exitrd_persistent.py" \
            --install --expected-sha256 "$HASH" \
            --risk-ack I_ACCEPT_EARLY_SAFE_SCREEN_RISK
        exit
        ;;
    --disable)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        if systemctl is-enabled --quiet sonoscdplayer-exitrd.service; then
            echo 'Persistent installation active: use --uninstall, not --disable.' >&2
            exit 1
        fi
        if ! mountpoint -q /run/initramfs; then
            echo 'No staged one-boot exitrd detected. Nothing to deactivate.'
            exit 0
        fi
        sudo "$PYTHON" "$ROOT/tools/exitrd_once.py" --deactivate
        exit
        ;;
    --status|--enable-once)
        if [ "$#" -eq 3 ] && [ "$2" = "--config" ] && [ -n "$3" ]; then
            CONFIG=$3
        elif [ "$#" -ne 1 ]; then
            usage >&2
            exit 2
        fi
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

"$PYTHON" "$ROOT/tools/exitrd_player_config.py" --config "$CONFIG"
if systemctl is-enabled --quiet sonoscdplayer-exitrd.service; then
    echo "Persistent boot service: ENABLED"
else
    echo "Persistent boot service: disabled"
fi
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
if systemctl is-enabled --quiet sonoscdplayer-exitrd.service; then
    echo "Refusing: persistent service enabled; use --uninstall first" >&2
    exit 1
fi
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
