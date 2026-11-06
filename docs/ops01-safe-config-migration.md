# OPS-01 — Safe configuration migration (design gate)

Status: initial design and acceptance criteria; **no production migration or cutover**.

## Boundary

- Development checkout: `/home/pi/src/sonoscdplayer`.
- Production installation: `/home/pi/sonoscdplayer`.
- Actual production player config (systemd-verified): `/home/pi/sonoscdplayer/player.toml`.
- Do **not** assume these locations are symlinks or that `src/config.toml` is
  the live config. Determine paths from `systemctl show ... -p ExecStart`.
- A deployment must never copy the repository's example config over a local
  config, nor modify a private UID file, GPIO pin settings, socket permissions
  or production service overrides implicitly.
- Do not commit any local configuration, UID or private operational tracker to Git.

## Planned migration behavior

1. **Read-only inventory:** inspect effective service ExecStart, resolved
   working directories and config paths; list presence/permissions without
   reading or logging UID secrets. No service restart, motor action, Sonos RPC
   or file modifications.
2. **Parse and validate:** read old and new TOML with `tomllib`; reject
   malformed tables, invalid values and incompatible/unknown settings rather
   than silently discarding them.
3. **Deterministic preview:** show only sanitized structural changes:
   new keys with proposed defaults, existing key preservation, changed types,
   deprecated keys, and a clear block/warning for security-sensitive settings.
   Never expose tokens, Sonos identifiers or arbitrary config values.
4. **Explicit opt-in apply:** only during a separately approved maintenance
   window. Precondition: no active CD reader, owned stream, group recovery or
   conflicting player process. Backup original config to a private,
   timestamped file with restrictive permissions; write a validated candidate
   to a new temporary file in the same directory and atomically replace only
   the explicitly selected target.
5. **No implicit activation:** a newly introduced optional feature such as
   `drive_power.idle_spin_down_seconds` must remain disabled unless the
   operator knowingly opts in. Likewise no implicit `--execute` or
   `--drive-activity-events` on the button service.
6. **Post-apply verification and rollback:** compare checksums, reparse file,
   validate service arguments and expose a reversible restore path. Never
   auto-start services or send motor/Sonos actions.

## Required tests before an implementation is usable

- Read-only dry-run does not touch the filesystem or leak config values.
- Existing keys (including nested tables, `drive_power`, GPIO, locale and
  application-specific extras) survive without loss.
- New optional safety features default to off and are not added as active
  settings without consent.
- Malformed/unknown or conflicting configuration fails closed.
- Missing config, symlinks, permission errors, atomic-write failure and
  rollback failure are handled safely.
- Backup permissions are owner-only; no secrets in logs or Git.
- Dry-run works without GPIO, CD drive, SoCo, systemd or other hardware.

## M0.19 cross-check

The offline M0.19 tests were reported green (292 total, 2026-11-04).
Spin-down STOP was acknowledged on the isolated player, but the subsequent
START coincided with USB over-current/disconnect and failed. M0.19 hardware
acceptance remains **blocked** pending the externally powered USB hub.
Neither this document nor OPS-01 authorizes a production configuration edit.


## Implemented read-only preview (first slice)

The tool `src/tools/config_dry_run.py` compares the current configuration's
TOML **structure and types**, not its values. It does not write backups or
apply migrations; it does not restart services or contact the CD drive or Sonos.
Its `--service` option reads the effective systemd ExecStart with
`systemctl show` and resolves the absolute `--config` value.

From the **development** checkout on the Raspberry Pi:

```sh
cd ~/src/sonoscdplayer
python3 src/tools/config_dry_run.py \
  --service sonoscdplayer-player.service \
  --template src/config.toml
```

Alternatively provide explicit files:

```sh
python3 src/tools/config_dry_run.py \
  --current /path/to/local.toml \
  --template src/config.toml
```

Only names of missing reference keys, type conflict categories and counts of
unknown local keys are printed. No existing values or private paths are
printed. Exit codes: **0** structurally compatible, **1** manual review
required (unknown keys/type conflicts), **2** inspection error. Existing
values are **not** validated against current runtime semantics; a compatible
report is not permission to apply or restart services.

```sh
PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_ops01_*.py' -v
```

The historical production directory has its own configuration; never copy
the reference configuration over that file. The apply/rollback phase
remains **unimplemented**.


## Read-only migration proposal (second slice)

Use the new `--plan` switch to inspect **migration eligibility** without
writing anything:

```sh
cd ~/src/sonoscdplayer
python3 src/tools/config_dry_run.py \
  --service sonoscdplayer-player.service \
  --template src/config.toml \
  --plan
```

The planner has a deliberately narrow allowlist of **candidate proposals**:
`audio_startup.pcm_timeout_seconds = 45` and
`led_outputs.ambient.timeout_ms = 150`, only if the template value exactly
matches the audited value and the key is missing. A candidate is **never
applied automatically**, does not authorize deployment and requires separate
review. Existing settings are never changed.

Any missing key under `service`, `drive_power`, GPIO, button and encoder
settings is blocked as protected, even if its template value would otherwise
look harmless. Thus `service.experimental_safe_screen` and
`drive_power.idle_spin_down_seconds` cannot be implicitly enabled.
Other unknown missing keys are counted without disclosure. Conflicting
types require review; arbitrary user key names and all values remain redacted.
The output contains only approved candidate names and aggregate counts.

Exit status: **0** no differences requiring a decision; **1** proposed
candidates or other required review; **2** file or inspection failure. The
tool does not compare existing values, perform deep semantic validation,
generate rewritten TOML, modify files, or restart services.

```sh
PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_ops01_*.py' -v
```

**Reference discrepancy resolved during review:** The current
`main` reference defines `service.experimental_safe_screen = false`, which
explains the observed missing protected key in production. The original
OPS-01 branch predates the experimental shutdown feature. The option must
remain protected and must never be silently injected, enabled or disabled by
the migration planner. Review coverage includes both missing disabled
defaults and preservation of any existing local choice.

**Review integration:** The reviewed OPS-01 files have been carried onto a
fresh branch from the current `main`. This avoids merging the former
OPS-01 branch's 172-commit divergence. M0.19 remains independently gated.
No apply or rollback implementation is included.

## OPS-01 completion workstream (2026-11-05)

The read-only inspector and planner from PR #8 are an accepted first slice,
**not** a complete migration. Development on
`feature/ops01-safe-config-apply` must preserve the following gates.

### Phase 1: offline apply/rollback implementation

- Separate `--plan` from `--apply` and `--rollback`. Never infer permission to
  write from a structurally compatible report. Existing read-only commands
  must retain their behavior and exit codes.
- No generic merge of template TOML into production. Only audited, inert,
  allowlisted new keys may be proposed; protected subtrees (`service`,
  `drive_power`, GPIO, buttons, encoder, and identity/UID) are never
  changed by the automated writer.
- Before any writes, construct a proposed candidate and validate its parsed
  contents against the original plus the individually authorized additions.
  Preserve every existing value, table, and non-allowlisted extra key.
  Reject unsupported syntax/ambiguous placement, invalid TOML, or unexpected
  candidate differences; never silently rewrite the entire local file.
- Resolve the effective target from systemd, inspect path and parent directory
  without following symlinks, and reject nonregular files, permissions errors,
  unexpected owner/mode, and hard-link ambiguities. The exact expected
  original checksum is an apply precondition to prevent stale plans.
- Require distinct, explicit operator consent for the exact candidate and
  target. A CLI flag alone is not permission to stop services, send Sonos
  commands, or apply any unrelated settings.
- Before applying, verify the player is idle (`STOPPED`, no active reader or
  stream, no Sonos ownership or pending group recovery); reject stale,
  unknown, or unavailable state. Detect concurrent modifications by verifying
  original identity/checksum immediately before replacement.
- Backup the unmodified source with owner-only permissions, sync the backup,
  write a private temporary candidate in the same directory, reparse it,
  atomically replace the target, and sync the parent directory. On failures
  fail closed; if restoration is needed, use a separately validated backup
  path and atomic restore. Never restart services automatically.
- Explicit rollback requires backup identity/integrity and an expected
  checksum for the current target; reject any other drift. Keep immutable
  backup provenance and do not print contents, credentials or UIDs.
- Never apply or rollback against the live
  `/home/pi/sonoscdplayer/player.toml` without a separate approved
  maintenance window and a dry-run + offline test record.

### Phase 2: required verification

- Test happy-path apply and rollback solely in temporary directories with
  synthetic TOML, including nested keys and unrelated local extras.
- Test stale checksum, symbolic/hard links, permissions, nonregular files,
  malformed TOML, protected keys, changed template defaults, and injected
  failure before and after backup/replace. Verify no accidental write and
  no secret exposure, including error messages.
- Test explicit gates against absent/active/unknown player status and pending
  group recovery; exercise crash-safe rerun/rollback semantics.
- Run `PYTHONPATH=src /home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s tests`
  on the Pi without changing production services. Review the diff and
  release documentation before a separately approved live rehearsal.

The existing production `m016b-config.conf` and `m019-venv.conf` overrides
are not managed by OPS-01. No button `--execute`, OLED RPC, drive-power
change, or shutdown-hook setting is implied by completion of these phases.

### Security-review checkpoint (2026-11-06)

The first sandbox-only implementation has been exercised on the Raspberry Pi:
**503/503 offline tests passed** at commit `3eff89c`, including actual child
`SIGKILL` at the atomic-replace boundary and inspection from a newly started
Python process that inherits an already-open disposable directory descriptor.

Additional hardening in the same feature branch is under test:
- Independent recovery inspection rejects artifacts with group/other file
  permissions, including the backup, intent journal and replacement.
- A completed sandbox rollback retains its journal and rejects another apply;
  the transaction is deliberately **one-shot**.
- No production path is accepted by the sandbox apply/rollback interface.

**Unresolved production blockers:** Existing checksum checks do not eliminate
the time-of-check/time-of-use gap between the final pathname inspection and
`os.replace`. A production implementation needs exclusive, independently
verified coordination with all potential writers, safe target-directory
ownership and reopening, durable lifecycle/provenance rules, failure-injection
around `fsync`, and a fail-closed live player/reader/ownership preflight.
The current offline journal is not authenticated against an attacker who can
write its private directory. A subprocess inheriting a directory FD is not a
full reboot/reopen test. No production `--apply` or `--rollback` is exposed.

The safety-review test count must be updated from actual Pi results before
claiming this checkpoint complete. No status change to OPS-01 until the
production acceptance criteria and separately approved rehearsal pass.

### Fail-closed idle evidence (2026-11-06)

The player service's public `status` reply is **cached**, and its documented
snapshot reports `player.mode`, `sonos.owned`,
`sonos.group_cleanup_pending`, and `stream.active`. It does **not**
independently confirm reader activity, server/process state, ongoing commands,
or every remaining group membership. A `STOPPED` status is therefore
**insufficient** to authorize configuration replacement.

`src/tools/config_idle_evidence.py` is a pure offline diagnostic assessment
with additional hypothetical controller-level evidence. It returns
`write_authorized=false` unconditionally, even when all supplied values are
consistent with idle. Live freshness, exclusive coordination, safe filesystem
path handling and actual maintenance authorization remain separately required.
Do not wire this diagnostic result directly to a writer.

The OPS-01 implementation remains sandbox-only and the pull request remains
Draft. The most recent Pi-wide verified test result before this addition was
**507/507**, at commit `259e99e`; newer tests require their own Pi run.

### Cooperative sandbox writer lock (2026-11-06)

A stable, private `ops01-writer.lock` is now held using a nonblocking POSIX
`flock` during sandbox seed/apply/rollback. The lock file is never unlinked
and must remain an owner-owned regular file with mode `0600` and one link.
A second cooperating process must fail closed while the lock is held.

This is an **advisory lock only**. External tools that edit `player.toml`
without acquiring the same lock can still race between the pathname identity
check and `os.replace`. It does not remove that TOCTOU problem or prove a
safe production writer. The lock accepts an already-open sandbox directory
descriptor only and does not enable production apply.

The five new lock tests require confirmation on the Raspberry Pi. The last
confirmed full regression before this change was **515/515** at `46b8136`.

### Maintenance-window evidence policy (2026-11-06)

At commit `94e7139`, the Raspberry Pi passed **520/520** regression tests,
including cooperative sandbox advisory-lock tests.

The new pure function `assess_maintenance_evidence` models claims that an
operator has approved a particular maintenance window, the player service,
listener, reader and player process are inactive, group recovery is clear,
private paths and a cooperative writer lock are verified, and both the
original and candidate SHA-256 values match the planned transaction.
Missing, inconsistent or ambiguous claims fail closed.

**It does not perform a live preflight.** All evidence is supplied by the
caller and is not independently attested, cannot prove other writers are
excluded, and cannot establish freshness or guard the file-swap boundary.
Accordingly `write_authorized` is **always false**, including when every
claim is consistent. The function is not wired to the sandbox writer or any
real config path. Tests for this addition must pass before it can be counted
as an accepted OPS-01 substep.

Before allowing production apply we still need a separately approved
maintenance protocol, an exclusive cross-service coordination mechanism,
robust path/permission rules and a transactionally safe race strategy,
plus explicitly scoped failure tests. A successful offline gate is not
authorization to stop the player or migrate `player.toml`.
