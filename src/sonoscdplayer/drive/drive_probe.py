#!/usr/bin/env python3
"""Find the drive that holds an audio CD.

Each optical drive is checked with cd-info. Run as a program it prints the
result per drive and ``SELECTED_DEVICE=/dev/srN`` for the one audio-CD drive.
"""
import glob
import os
import re
import subprocess
import sys


def probe_drive(path):
    """(True, description) if the drive at ``path`` holds an audio CD."""
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


def find_audio_cd_drive(*, report=print, probe_drive=probe_drive,
                        block_root="/sys/class/block") -> str:
    """Return the one drive (``/dev/srN``) that holds a readable audio CD.

    Every optical drive is probed with cd-info; ``report`` receives one line
    per drive.

    Raises:
        RuntimeError: no drive, no drive with an audio CD, or more than one.
    """
    paths = sorted(glob.glob(os.path.join(block_root, "sr*")),
                   key=lambda p: int(os.path.basename(p)[2:]) if os.path.basename(p)[2:].isdigit() else 9999)
    if not paths:
        raise RuntimeError("no optical drive found")
    valid = []
    for sys_path in paths:
        device_path = "/dev/" + os.path.basename(sys_path)
        if not os.path.exists(device_path):
            report(f"SKIP {device_path}: device node missing")
            continue
        ok, description = probe_drive(device_path)
        report(f"{'PASS' if ok else 'SKIP'} {device_path}: {description}")
        if ok:
            valid.append(device_path)
    if not valid:
        raise RuntimeError("no drive with a readable audio CD")
    if len(valid) > 1:
        raise RuntimeError("several drives hold an audio CD; select one explicitly")
    return valid[0]


def main():
    """Command line: print the selected drive; exit code 1 if there is none."""
    try:
        device = find_audio_cd_drive()
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"SELECTED_DEVICE={device}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
