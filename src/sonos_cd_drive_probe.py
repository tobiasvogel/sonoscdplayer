#!/usr/bin/env python3
"""Read-only optical-drive discovery for SonosCDPlayer v0.11.0-alpha.1.

Disc type is verified with cd-info; this probe never starts audio playback.
"""
import glob
import os
import re
import subprocess
import sys


def probe(path):
    try:
        result = subprocess.run(
            ["cd-info", "--no-analyze", "--no-device-info", path],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "cd-info timed out"
    except FileNotFoundError:
        return False, "cd-info missing (install libcdio-utils)"
    except OSError as exc:
        return False, str(exc)

    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        return False, f"cd-info exit={result.returncode}: {output.strip()[-250:]}"
    if not re.search(r"Disc mode is listed as:\s*CD-DA\b", output):
        return False, "media is not recognized as CD-DA"
    tracks = re.findall(r"^\s*\d+:\s+\d\d:\d\d:\d\d\s+\d+\s+audio\b", output, re.M)
    if not tracks:
        return False, "CD-DA reported, but no audio tracks found"
    return True, f"CD-DA, {len(tracks)} audio tracks"


def main():
    paths = sorted(glob.glob("/sys/class/block/sr*"), key=lambda p: int(os.path.basename(p)[2:]) if os.path.basename(p)[2:].isdigit() else 9999)
    if not paths:
        print("FAIL: no optical drives in /sys/class/block")
        return 1

    valid = []
    for sys_path in paths:
        name = os.path.basename(sys_path)
        device_path = "/dev/" + name
        if not os.path.exists(device_path):
            print(f"SKIP {device_path}: device node missing")
            continue
        ok, description = probe(device_path)
        print(f"{'PASS' if ok else 'SKIP'} {device_path}: {description}")
        if ok:
            valid.append(device_path)

    if len(valid) == 1:
        print(f"SELECTED_DEVICE={valid[0]}")
        return 0
    if len(valid) == 0:
        print("FAIL: no drive with readable audio CD")
    else:
        print("FAIL: multiple audio-CD drives; explicit selection required")
    return 1


if __name__ == "__main__":
    sys.exit(main())
