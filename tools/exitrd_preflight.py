#!/usr/bin/env python3
"""Read-only exitrd preflight and explicit, non-activating RAM staging.

This tool NEVER writes to /run/initramfs and NEVER changes systemd units.
The staged directory is only a test artifact, not a usable exitrd installation.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import stat
import struct
import sys

MAX_BINARY_BYTES = 32 * 1024 * 1024
MACHINE_NAMES = {62: "x86_64", 183: "aarch64"}
ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


class PreflightError(ValueError):
    pass


def native_arch() -> str:
    arch = platform.machine().lower()
    return ALIASES.get(arch, arch)


def inspect_elf(data: bytes) -> str:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise PreflightError("not an ELF executable")
    if data[4] != 2 or data[5] != 1:
        raise PreflightError("requires 64-bit little-endian ELF")
    kind, machine = struct.unpack_from("<HH", data, 16)
    if kind not in (2, 3) or machine not in MACHINE_NAMES:
        raise PreflightError("unsupported ELF type or architecture")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    if phnum == 0 or phentsize < 56 or phnum > 1024:
        raise PreflightError("invalid ELF program-header table")
    if phoff > len(data) or phnum * phentsize > len(data) - phoff:
        raise PreflightError("truncated ELF program headers")
    for i in range(phnum):
        off = phoff + i * phentsize
        ptype, _flags, offset, _vaddr, _paddr, filesz, _memsz, _align = (
            struct.unpack_from("<IIQQQQQQ", data, off))
        if ptype == 3:  # PT_INTERP: needs a dynamic loader after switch-root
            raise PreflightError("dynamic ELF interpreter is not exitrd-safe")
        if ptype in (1, 2) and (offset > len(data) or filesz > len(data) - offset):
            raise PreflightError("truncated ELF segment")
        if ptype == 2:  # PT_DYNAMIC: reject DT_NEEDED even without PT_INTERP
            if filesz % 16:
                raise PreflightError("malformed ELF dynamic table")
            for at in range(offset, offset + filesz, 16):
                tag, _value = struct.unpack_from("<QQ", data, at)
                if tag == 1:  # DT_NEEDED
                    raise PreflightError("external shared-library dependency")
                if tag == 0:
                    break
    return MACHINE_NAMES[machine]


def read_binary(binary: Path) -> tuple[bytes, str]:
    if binary.is_symlink():
        raise PreflightError("binary must not be a symlink")
    info = binary.stat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise PreflightError("binary must be a regular executable")
    if info.st_mode & 0o022:
        raise PreflightError("binary must not be group/world writable")
    if info.st_size < 64 or info.st_size > MAX_BINARY_BYTES:
        raise PreflightError("binary size outside allowed range")
    data = binary.read_bytes()
    if len(data) != info.st_size:
        raise PreflightError("binary changed during inspection")
    return data, inspect_elf(data)


def unescape_mount_path(encoded: str) -> str:
    data = bytearray()
    i = 0
    while i < len(encoded):
        if encoded[i] == "\\":
            octal = encoded[i + 1:i + 4]
            if len(octal) != 3 or any(x not in "01234567" for x in octal):
                raise PreflightError("malformed mountinfo escape")
            number = int(octal, 8)
            if number == 0:
                raise PreflightError("NUL in mount path")
            data.append(number)
            i += 4
        else:
            data.extend(encoded[i].encode("utf-8"))
            i += 1
    return data.decode("utf-8", errors="strict")


def mount_fs_type(path: Path, mountinfo: str) -> str:
    """Find backing mount with longest component-aligned prefix."""
    resolved = str(path.resolve(strict=True))
    found = (-1, None)
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            sep = fields.index("-")
            if sep + 1 >= len(fields) or len(fields) < 6:
                raise ValueError("bad mountinfo")
            mountpoint = unescape_mount_path(fields[4])
            fstype = fields[sep + 1]
        except (IndexError, ValueError) as exc:
            raise PreflightError("malformed mountinfo") from exc
        if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
            if len(mountpoint) > found[0]:
                found = (len(mountpoint), fstype)
    if found[1] is None:
        raise PreflightError("target is not represented in mountinfo")
    return found[1]


def inspect_stage_dir(target: Path, mountinfo: str) -> Path:
    if target.is_symlink() or not target.is_dir():
        raise PreflightError("stage directory must exist and not be a symlink")
    real = target.resolve(strict=True)
    live = Path("/run/initramfs")
    if real == live or live in real.parents or real in live.parents:
        raise PreflightError("cannot stage into or above /run/initramfs")
    if mount_fs_type(real, mountinfo) != "tmpfs":
        raise PreflightError("stage directory must be backed by tmpfs")
    if (real / "shutdown").exists() or (real / "shutdown").is_symlink():
        raise PreflightError("staging destination already exists")
    return real


def write_staged_binary(target: Path, data: bytes) -> Path:
    """Create a new shutdown executable; never overwrite any staged binary."""
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        output = os.open("shutdown", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o700, dir_fd=fd)
        created = True
        try:
            with os.fdopen(output, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink("shutdown", dir_fd=fd)
            raise
    finally:
        os.close(fd)
    return target / "shutdown"


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--expected-arch", choices=("native", "aarch64", "x86_64"),
                        default="native")
    parser.add_argument("--stage-dir", type=Path)
    parser.add_argument("--stage", action="store_true",
                        help="explicitly copy to an isolated tmpfs directory; NEVER activate")
    args = parser.parse_args(argv)
    if args.stage and args.stage_dir is None:
        parser.error("--stage requires --stage-dir")
    data, arch = read_binary(args.binary)
    expected = native_arch() if args.expected_arch == "native" else args.expected_arch
    if arch != expected:
        raise PreflightError(f"ELF architecture {arch}, expected {expected}")
    digest = hashlib.sha256(data).hexdigest()
    print(f"ELF: static/no-needed, arch={arch}, bytes={len(data)}, sha256={digest}")
    if args.stage_dir is not None:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        real = inspect_stage_dir(args.stage_dir, mountinfo)
        print(f"Isolated RAM stage: {real} (tmpfs, non-activating)")
        if args.stage:
            staged = write_staged_binary(real, data)
            print(f"STAGED: {staged} (NOT /run/initramfs/shutdown)")
    else:
        print("CHECK ONLY: no staging requested")
    print("NOT VERIFIED: PID1 switch-root, /oldroot unmount, OLED SPI/GPIO, poweroff")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (OSError, PreflightError) as exc:
        print(f"exitrd preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
