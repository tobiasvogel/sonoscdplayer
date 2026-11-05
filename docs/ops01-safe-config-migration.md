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
