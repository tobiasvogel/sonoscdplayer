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

## 2026-11-05 operator-observed ACT LED shutdown signal

In the two previously supervised real poweroff tests, the operator observed approximately ten short ACT LED flashes followed by the green LED staying off, at about the same time as the final OLED artwork was displayed. The Pi subsequently booted normally. This is stronger external confirmation of normal completion than simply observing an absence of storage activity. Raspberry Pi's official LED documentation says that on Raspberry Pi models 1–4 the short ACT LED flash sequence following `sudo poweroff` indicates that it is safe to disconnect the USB power supply: https://www.raspberrypi.com/documentation/computers/configuration.html

**Temporal distinction:** The standard final OLED frame can be drawn *before* the final kernel poweroff call and before the firmware ACT LED sequence finishes. Therefore the OLED frame appearing or remaining visible must **not** be interpreted as an independently synchronized safe-to-unplug signal. Operator-facing manual guidance: after a deliberately initiated shutdown, wait until the distinctive ACT LED shutdown sequence has completed and ACT remains off, then disconnect supply if desired, subject to the behavior of the installed Pi model and attached devices. This is a documented normal-shutdown indication, not a guarantee against microSD firmware failure.

The feature-branch exitrd renderer stays locked to `safe-approved=0`; its safe frame must not be enabled by this observation. No GPIO/firmware LED monitor, extra circuitry, persistent exitrd staging or automatic power action has been added. The exact board model has not been independently established in this record, so the applicability of the Raspberry Pi 1–4 LED documentation must be checked for this unit before formal acceptance.

## Explicitly opt-in *unverified* Safe-to-unplug screen (experimental, not enabled)

**Risk warning:** The existing `safetounplug_screen.png` artwork can appear **before the kernel's final poweroff syscall and before the Raspberry Pi's ACT LED shutdown sequence has finished**. A fixed three-second wait **does not certify storage durability, the poweroff outcome, or that it is safe to unplug at the instant the screen appears**. The screen is an experimental convenience, not an objective safety indicator. The operator must still watch for the **completed** documented ACT LED sequence and ensure no unexpected device activity before unplugging.

The standard build remains unchanged and reports `unverified-safe=0 delay-seconds=0 safe-approved=0`. This is an **opt-in separate artifact**, never automatically installed or activated. To build the experimental variant deliberately:

```sh
EXITRD_UNVERIFIED_SAFE=1 \
EXITRD_UNVERIFIED_SAFE_ACK=I_ACCEPT_EARLY_SAFE_SCREEN_RISK \
sh tools/build_exitrd.sh
build/exitrd-unverified-safe/shutdown --check
```

Default `build/exitrd/shutdown` is unaffected. The experimental artifact is `build/exitrd-unverified-safe/shutdown`, compiled with `SONOSCD_EXITRD_UNVERIFIED_SAFE=1`. `SONOSCD_EXITRD_SAFE_APPROVED` stays hard-locked to `0`; the build rejects diagnostic mode in combination with the experimental graphic. Omitting the explicit risk acknowledgement rejects the build before producing a binary.

The experimental runtime draws the normal final frame first. **Only for a true poweroff action**, after successful `release_oldroot()`, a parseable post-unmount mount audit with `persistent=0` and `oldroot=0`, and completed `sync()`, it waits three seconds and then draws the pre-existing `Safe to unplug` artwork immediately before the final kernel poweroff syscall. On unmount failure, dirty/failed audit, interrupted delay, reboot or halt, the experimental graphic is not drawn. If the poweroff syscall unexpectedly returns, the runtime repaints the normal final screen before reporting the error. This fallback cannot prevent all possible incorrect impressions because a system may fail between drawing and entering the syscall.

The conservative evidence-state simulator **continues to say `safe-eligible=0` in every case**: the experimental picture is not a safety approval. The previous two hardware acceptance tests were for the normal/diagnostic exitrd, not this artifact. The experimental artifact has not yet been boot-tested; any one-boot staging would require a separately supervised opt-in, freshly verified SHA-256 and no automatic shutdown. Do not stage this mode as a persistent default, and do not merge it to `main` without explicit project approval.

## 2026-11-05 experimental-build 54-test offline acceptance

The operator pulled the source-assertion correction from feature branch commit `e854c25` and executed the `test_exitrd_*.py` regression suite on the Raspberry Pi: **54 tests passed in 6.722 seconds**. The separately built experimental binary's offline check returned:

```text
exitrd: offline check; backend=ssd1327 format=gray4-packed safe-approved=0 diagnostic=0 unverified-safe=1 delay-seconds=3
```

This confirms the **offline regression suite and binary build flags**, not the timing, OLED transition, systemd PID-1 handover or final ACT LED signal for this experimental variant. The opt-in binary is **not** automatically staged, and the prior supervised real poweroff tests used different builds. The existing `Safe to unplug` artwork still appears *before* the final poweroff syscall in this opt-in path, even with the three-second delay; the operator must not unplug based on the OLED text alone. Explicit one-boot staging and an operator-controlled physical test require a separate deliberate decision. No persistent configuration was enabled.

## 2026-11-05 supervised experimental 3-second exitrd poweroff acceptance

**Operator report:** The first supervised live shutdown with the one-boot-staged experimental binary was successful (“einwandfrei geklappt”). The Raspberry Pi subsequently started normally and is operational again. Before the test, the operator verified the static aarch64 binary, its exact SHA-256 `2034b3beb60e117cdda7915636dc6082f43a7170c68eaca7d4f926a6b48340a0` both before and after staging, and `--check` reporting `safe-approved=0 diagnostic=0 unverified-safe=1 delay-seconds=3`. The isolated ram-root activation reported `ARMED FOR THIS BOOT ONLY` and did not trigger shutdown. The operator then performed a supervised poweroff and confirmed the test was successful, followed by a successful normal boot.

**Evidence limits:** The operator did not supply detailed time stamps, a video, or a per-stage transcript of OLED transitions and ACT LED sequence in this particular report; do not claim independently instrumented three-second timing or flash-controller durability. The previously observed ACT LED shutdown blink sequence provides separate supporting operator evidence, but must not be silently attributed to this exact trial. Most importantly, the opt-in “Safe to unplug” frame is still displayed **before** the final kernel poweroff syscall. It is **not** an independently verified safe-to-disconnect indication. The operator must still wait for completion of the ACT LED shutdown sequence before removing power. `SONOSCD_EXITRD_SAFE_APPROVED=0` remains hard-locked.

The tested activation was RAM-only and nonpersistent; a normal reboot removes it. No permanent feature configuration or systemd staging unit was enabled. `main` remains unchanged. Next design task: an explicit default-off operator-controlled configuration for experimental staging with opt-in risk acknowledgement, rollback and observability, without converting the experimental frame into a safety claim.

## Configuration decision correction — 2026-11-05

The stand-alone `config/shutdown.ini` and `tools/exitrd_config.py` implementation was withdrawn as inconsistent with SonosCDPlayer's existing `config.toml` convention. It would duplicate the project's configuration mechanism and introduce an unexpected file format. No INI file should be created on the Raspberry Pi. The `test_exitrd_config.py` tests for that rejected helper were also removed.

**Pending:** Locate and inspect the actual deployed TOML configuration path, existing parser, schema/default handling, and service start-up behavior before integrating the experimental shutdown option in the established configuration. A proposed default is `[shutdown] experimental_safe_screen = false`, but this is **not yet implemented**; do not describe it as available. Keep shutdown staging as a separate, explicit, opt-in administrative operation with SHA checking and risk acknowledgements, not a side effect of merely loading configuration.

Previously tested opt-in manual build and `exitrd_once.py` one-boot activation remain available and unchanged. `safe-approved=0` and ACT LED completion requirements remain unchanged. `main` remains untouched.

## Existing player TOML integration (default off)

Shutdown display selection belongs in **`config/player.example.toml`**, in the existing `[service]` table beside `shutdown_policy`, **not** in `ui.example.toml` or any additional INI file:

```toml
[service]
shutdown_policy = "stop_owned"
experimental_safe_screen = false
```

The sample defaults to false. Older deployed player TOML files without this option also default to false. The existing service/player loader is **not** changed to arm anything; a local `true` only enables a separate **explicit administrator action**. No new service, auto-install, recurring boot staging, or automatic poweroff is introduced. The separate `tools/exitrd_player_config.py` helper accepts the location of the actual deployed player TOML via mandatory `--config`, avoiding guesses about where the installation stores it. Python 3.11+ `tomllib` strictly parses the file; missing/symlinked/invalid files and non-boolean values fail closed.

After updating your *actual* player TOML with `experimental_safe_screen = true` under `[service]` and building the separate acknowledged experimental artifact, inspect the selection read-only with:

```sh
python3 tools/exitrd_player_config.py --config /ACTUAL/PATH/player.toml
```

For a deliberately approved **one-boot activation only**:

```sh
SHA=$(sha256sum build/exitrd-unverified-safe/shutdown | awk '{print $1}')
sudo python3 tools/exitrd_player_config.py \
  --config /ACTUAL/PATH/player.toml \
  --activate-once --expected-sha256 "$SHA" \
  --acknowledge I_UNDERSTAND_EXITRD_CAN_PREVENT_CLEAN_SHUTDOWN \
  --acknowledge-risk I_ACCEPT_EARLY_SAFE_SCREEN_RISK
```

The helper checks the expected binary's offline mode string (`safe-approved=0 diagnostic=0 unverified-safe=1 delay-seconds=3`), the real ELF/SHA/host preflight through the established once-helper, then stages only into RAM for one shutdown. To roll back before shutdown: `sudo python3 tools/exitrd_player_config.py --config /ACTUAL/PATH/player.toml --deactivate`; rollback is possible even if the TOML was removed/corrupted. Editing the player TOML back to false by itself does not undo an already staged exitrd.

**Important:** This is a manual administration interface, not yet integration with the player's live settings UI or its normal config loader. The opt-in `Safe to unplug` bitmap is still drawn *before* the final kernel poweroff syscall. Wait until the ACT LED's documented shutdown sequence has fully ended before disconnecting power. The option does not create a verified safe-to-unplug state. The new helper and tests are not yet accepted on the Pi.
