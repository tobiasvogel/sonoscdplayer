# Experimental exitrd one-boot staging

`tools/exitrd_once.py` is **read-only by default**. It does not install a systemd service or trigger shutdown.

The explicit activation option must not be used until a supervised recovery plan has been agreed. Activation temporarily mounts an executable RAM filesystem on `/run/initramfs`, preserving copies of the existing fsck files and requiring both an expected SHA-256 digest and an acknowledgement token. The original files remain underneath the mount; the `--deactivate` option restores visibility before shutdown. The mount is not persistent across boots.

**Fallback change (source only):** The final-screen shell and static C hooks now render the `final` frame even when exitrd is staged; a working exitrd may repaint after `/oldroot` is released. The already-installed static hook on the Pi is not changed by pulling source. Rebuild, verify, and explicitly install the revised C binary before a live test. A fallback image is not proof of filesystem safety. The exitrd's `safe-approved` flag stays `0`. This sentence was written before the supervised 2026-11-05 hardware test; see the acceptance record below for the current status.

Only after offline checks and an isolated staging/rollback test should a supervised live shutdown be considered. Do not arm during normal operation.

The default-mode and activation-gating regression cases are included in `tests/test_exitrd_preflight.py`. Run those tests and the shell/static hook tests before any installation. A successful source-level test does not replace an installed-hook hash check.

## PID-1 live-handover staging requirements

The staging helper now creates the required `oldroot`, `dev`, `proc`, `sys` and `run` directory targets in the separate RAM root. Exitrd reports its PID-1 entry and oldroot unmount result on the shutdown console (`sonoscd-exitrd: ...`). Such console messages **do not persist automatically across poweroff**; record them through an already configured external console before claiming the handover was verified. The pre-exitrd final OLED frame is a fallback, not diagnostic proof.

Before any further live handover, rebuild `build/exitrd/shutdown`, run the offline tests and re-run isolated staging/rollback against the new build. Do not use an old digest for activation. Do not activate `safe-approved=1`.

## Opt-in OLED visual proof for first supervised poweroff

The default `sh tools/build_exitrd.sh` produces `build/exitrd/shutdown`, with `diagnostic=0`. It retains the usual final frame after successful `/oldroot` unmount.

For an **offline-only** diagnostic build use:

```sh
EXITRD_DIAGNOSTIC=1 sh tools/build_exitrd.sh
build/exitrd-diagnostic/shutdown --check
```

The second binary is stored separately at `build/exitrd-diagnostic/shutdown`. Its generated framebuffer has a white outline, contrasting bars and the labels `EXITRD / TEST / OK`. It is neither a confirmation of data durability nor a `Safe to unplug` screen. The build enforces `safe-approved=0` and rejects a diagnostic/safe-approved combination. The graphic is only shown by the PID-1 exitrd after `release_oldroot()` succeeds during poweroff; the previously installed static final hook should remain the fallback.

The image is an **in-band observation only**: seeing it is evidence that the program reached the post-unmount rendering path, not independent proof of physical SD-card safety or of the exact state of every storage device. Record a video of the OLED during the first supervised test. With no serial console, the precise PID-1 console diagnostics will not be preserved for audit. If the diagnostic frame does not appear, do not infer why from the OLED alone.

**No automatic activation:** the build and tests cannot arm `/run/initramfs/shutdown`, trigger `poweroff`, or touch the installed fallback hook. Always validate new binary hashes and the latest staging preflight before deciding on a separately approved live test. Do not set `safe-approved=1`.

## 2026-11-05 supervised Raspberry Pi poweroff acceptance

**Observed by operator:** With the separately built `diagnostic=1`, `safe-approved=0` binary staged under `/run/initramfs/shutdown`, the existing late C hook briefly displayed the `final` frame. The OLED then changed to `EXITRD / TEST / OK` and held this frame. The Raspberry Pi powered off normally, booted again, and the normal OLED interface returned.

**Interpretation:** The experimental exitrd reached its post-`release_oldroot()` success/display path during real shutdown; SPI/GPIO remained usable late enough to render the distinct test frame. The normal start after the next boot is also confirmed. This is one supervised successful end-to-end test, **not** an independent proof of every storage flush or a blanket safe-to-unplug authorization. There was no external serial console to capture PID-1 diagnostics. `SONOSCD_EXITRD_SAFE_APPROVED=0` remains mandatory.

**Next milestone:** Preserve the evidence and convert from the temporary test frame to a clearly non-safety normal final image only after controlled regression. Any future `Safe to unplug` label requires separate documented criteria and approval. Neither this result nor any source commit automatically enables persistent exitrd staging.

## Follow-up: regular final frame after validated diagnostic test

The ordinary exitrd already selects `shutdown_frame_final` (`src/data/pleasewait_screen.png`) when `diagnostic=0` and `safe-approved=0`; the special `EXITRD / TEST / OK` frame only exists in opt-in diagnostic builds. Two offline regression tests now assert that the normal generated framebuffer matches the final PNG, differs from the safe-to-unplug PNG, contains no diagnostic array, and that the offline build reports both switches disabled.

The next **separately supervised** poweroff should use the normal binary `build/exitrd/shutdown`, freshly built, verified via `--check` and expected SHA-256, and staged once with `tools/exitrd_once.py`. The installed C final hook remains untouched. Because the hook and ordinary exitrd display the same artwork, this follow-up test **cannot visually prove which process rendered the final frame**. Its purpose is to establish normal user-facing appearance and successful subsequent restart; the earlier distinct diagnostic frame remains the recorded evidence of post-unmount rendering. Do not claim a new independent PID-1 proof from unchanged artwork. This step is not a permanent installation or authorization for `safe-approved=1`.

## 2026-11-05 supervised normal-frame poweroff acceptance

**Operator-observed second live test:** The standard static aarch64 exitrd binary `build/exitrd/shutdown` (SHA-256 `97b2748f2a23dbeef0911c50d90b9cf67e7205fc5777c147e6ee2a8743b723a9`; `diagnostic=0`, `safe-approved=0`) was explicitly staged once at `/run/initramfs/shutdown`. Its staged SHA-256 matched. The known normal `final` artwork stayed visible until the Raspberry Pi powered off normally. After power-on, the Pi booted normally and the SonosCDPlayer interface returned.

**Evidence boundary:** Because both the installed static final hook and the ordinary exitrd draw identical artwork, this second test does not independently identify which process displayed the final frame. The previous opt-in distinct `EXITRD / TEST / OK` hardware test remains the evidence for reaching exitrd's post-unmount rendering path. Neither test independently proves stable-storage durability or justifies `Safe to unplug`. `safe-approved=0` remains enforced; no persistent staging mechanism has been installed and `main` has not been modified.

**Next acceptance gate:** Define and verify filesystem/device durability and absence of remaining writable block-device holders, plus failure-path behavior and poweroff-state criteria, before considering a distinct safety-approved screen. Do not equate a successful user-visible shutdown or boot with independent media-flush proof.

## 2026-11-05 safety-gate audit prototype (offline only)

The first safety review deliberately separates **observed mount cleanup** from **storage durability**. The static exitrd now includes a conservative post-unmount inventory of `/proc/self/mountinfo`: residual mounts under `/oldroot` are counted, and mount filesystems outside a small known volatile/pseudo-filesystem allowlist are counted as potentially persistent, including unknown and even read-only types. Audit parse errors are reported as failures. The read-only offline interface is:

```sh
build/exitrd/shutdown --audit-mountinfo /proc/self/mountinfo
```

This is **not** a live eligibility check: on a normally booted Pi it will correctly find storage-mounted filesystems. The C routine logs the audit when the PID-1 exitrd has successfully released `/oldroot`. It does not alter mount operations or change the final OLED artwork.

**Critical hard lock:** `SONOSCD_EXITRD_SAFE_APPROVED=1` now produces a compile-time error, and the C renderer no longer selects `shutdown_frame_safe`. The conservative gate returns `safe-eligible=0` even when all the listed mounts are volatile. Do not interpret a zero persistent-mount count as storage-level durability evidence. It is possible for writable block-device handles, in-flight I/O, controller caches, and failures in the physical medium to evade a mount-only inspection. Filesystem and block-layer ordering/flush semantics, active device holders, boot media layout, and failure behavior require separate criteria and tests before considering any future safe graphic.

The prior two supervised poweroffs tested the **earlier** binaries; this new audit change has not been hardware-tested and must not be activated until its offline tests pass. `main` remains untouched. No persistent exitrd activation was added.

## Shutdown evidence state machine (offline regression gate)

The experimental exitrd has an explicit **evidence state machine**, which records three independent results in strict order:

- `unmount=pending|ok|failed`
- `audit=skipped|clean|dirty|failed` (the audit is skipped if oldroot unmount failed)
- `poweroff=not-requested|requested|returned-error`

The kernel `reboot(2)` poweroff/halt/reboot request normally does not return after success; a return is explicitly classified as failure. `poweroff=requested` does **not** mean that physical power has been removed. These states are emitted only to the shutdown console at runtime, not persisted to the old root filesystem.

Use the no-side-effect offline reducer simulator for regression cases:

```sh
build/exitrd/shutdown --simulate-safety ok clean requested
build/exitrd/shutdown --simulate-safety failed clean requested
build/exitrd/shutdown --simulate-safety ok dirty requested
build/exitrd/shutdown --simulate-safety ok failed requested
build/exitrd/shutdown --simulate-safety ok clean returned-error
```

All combinations must report `safe-eligible=0`. The simulator does not unmount, render, activate exitrd, sync devices, reboot, or power off. The installed static C shutdown fallback and final OLED frame remain unchanged. The compile-time lock rejects `SONOSCD_EXITRD_SAFE_APPROVED=1` for any build. The prior successful hardware validation applies to the earlier exitrd code, **not** this new untested build.

This is an evidence tracker, not an authorization algorithm. Outstanding separate evidence: SD card firmware/controller persistence, device holder behavior beyond mount inventory, errors from sync/writeback, actual power state, and adverse shutdown cases. Do not make a safe-unplug claim based on successful unmount/audit alone.

## 2026-11-05 Raspberry Pi evidence-state offline acceptance

Operator ran the feature branch (commit `487bfc0`) on the Raspberry Pi, built the normal exitrd (`diagnostic=0`, `safe-approved=0`) and ran the `test_exitrd_*.py` suite: **49 tests passed in 4.072 s**. The five `--simulate-safety` cases printed:

```text
unmount=ok audit=clean poweroff=requested safe-eligible=0
unmount=failed audit=skipped poweroff=requested safe-eligible=0
unmount=ok audit=dirty poweroff=requested safe-eligible=0
unmount=ok audit=failed poweroff=requested safe-eligible=0
unmount=ok audit=clean poweroff=returned-error safe-eligible=0
```

The offline-only guard confirmed that no live exitrd was staged. No shutdown or power-cycle was performed in this acceptance. The test outcomes validate the simulator, not actual error injection under systemd PID 1; media-cache durability and actual power-off remain outside its evidence scope. `safe-approved=0` and no persistent activation remain mandatory.

## Isolated production-code failure-path acceptance (pending Pi run)

The isolated mount-namespace/chroot test `tools/test_exitrd_unmount_isolated.sh` now exercises **production C functions** `release_oldroot()`, `audit_mounts()`, and the shutdown evidence reducer directly, rather than relying exclusively on the `--simulate-safety` CLI. It creates three nested *tmpfs* mounts under a synthetic `/oldroot` and verifies:

- Before unmount, the mount audit sees exactly three `/oldroot` mounts, and the state machine refuses to record an audit before unmount succeeds.
- While the test process holds its cwd in a nested mount, production unmount must fail; audit remains skipped and `safe-eligible=0`.
- After releasing the cwd, the production unmount succeeds; post-unmount audit sees zero oldroot and zero persistent mounts, but `safe-eligible=0` remains.
- A simulated kernel poweroff syscall returning an error is explicitly classified `poweroff=returned-error`, still without safety authorization.

The test does not call the PID-1 main function, render the OLED, issue a real reboot/poweroff syscall, or mount at the host's `/run/initramfs`. The fixture is created exclusively inside a private mount namespace and chroot. This test **does not prove real device cache flushes, SD-card persistence, or the physical power state**. It is intentionally separate from the earlier two supervised poweroff trials.

## 2026-11-05 isolated production C integration acceptance

**Operator-observed Raspberry Pi test:** Following fast-forward to branch commit `3eb866f`, the normal exitrd compiled (`safe-approved=0`, `diagnostic=0`), and `bash tools/test_exitrd_unmount_isolated.sh` passed. The private mount namespace and chroot held three nested synthetic tmpfs mounts under `/oldroot`. The production `release_oldroot()`, `audit_mounts()` and state reducer produced:

```text
before=3 oldroot_present=1
busy_mount_rejected=1 oldroot_still_present=1
busy_evidence: unmount=failed audit=skipped poweroff=requested safe-eligible=0
after=0 oldroot_present=0
audit_after persistent=0 oldroot=0 safe-eligible=0
returned_power_evidence: unmount=ok audit=clean poweroff=returned-error safe-eligible=0
=== ISOLATED C UNMOUNT TEST PASSED ===
```

The full `test_exitrd_*.py` suite subsequently reported **49 tests passed in 4.095 seconds**. The host activation guard finished successfully; no exitrd was staged on the host, and no shutdown was triggered. This accepts integration of the production unmount, mount inventory and evidence-state failure handling **in a private tmpfs test fixture only**. It does not establish actual MMC durability, real shutdown failure injection, or power-loss safety. The safe graphical claim remains compile-time blocked.
