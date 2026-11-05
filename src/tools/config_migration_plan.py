"""OPS-01: conservative, value-redacted read-only migration proposal.

Only audited rules can generate candidates. The planner never edits TOML or
copies defaults into the production configuration. It does not import player,
Sonos, GPIO, or any hardware package.
"""
from __future__ import annotations

if __package__:
    from .config_dry_run import kind
else:
    # Same-directory import when launched as a standalone script.
    from config_dry_run import kind

# Opt-in, reviewed *proposals* only. These are not applied automatically.
# The expected value must match the audited default exactly (not merely type).
# Keep the table deliberately small until individual settings are reviewed.
APPROVED_DEFAULTS = {
    ('audio_startup', 'pcm_timeout_seconds'): 45,
    ('led_outputs', 'ambient', 'timeout_ms'): 150,
}

# Experiment flags, drive-power changes and hardware actions are always
# manual-review-only, regardless of their appearance in a reference file.
PROTECTED_PREFIXES = (
    ('drive_power',),
    ('service',),
    ('gpio',),
    ('buttons',),
    ('button_overrides',),
    ('encoder',),
    ('encoder_input',),
    ('input_defaults',),
    ('led_outputs', 'power_pin'),
    ('led_outputs', 'connection_pin'),
)


def _protected(parts):
    return any(parts[:len(prefix)] == prefix for prefix in PROTECTED_PREFIXES)


def plan_migration(current, template):
    """Return redacted, deterministic proposal. Never return config values.

    A candidate means "eligible for separate review", NOT authorized to
    write or to activate a feature.
    """
    if not isinstance(current, dict) or not isinstance(template, dict):
        raise ValueError('both configurations must be TOML tables')

    candidates = []
    protected_missing = 0
    unapproved_missing = 0
    type_conflicts = []
    extra_local_keys = 0
    existing_leaf_count = 0

    def visit(old, new, parts=()):
        nonlocal protected_missing, unapproved_missing
        nonlocal extra_local_keys, existing_leaf_count
        for key, expected in new.items():
            here = parts + (key,)
            if key not in old:
                if (here in APPROVED_DEFAULTS
                        and type(expected) is type(APPROVED_DEFAULTS[here])
                        and expected == APPROVED_DEFAULTS[here]
                        and not _protected(here)):
                    candidates.append('.'.join(here))
                elif _protected(here):
                    protected_missing += 1
                else:
                    unapproved_missing += 1
                continue

            existing = old[key]
            if kind(existing) != kind(expected):
                # Only show schema key names if explicitly audited.
                label = '.'.join(here) if here in APPROVED_DEFAULTS else None
                if label:
                    type_conflicts.append({
                        'key': label, 'current_type': kind(existing),
                        'template_type': kind(expected),
                    })
                else:
                    type_conflicts.append({'key': '[redacted]'})
                continue
            if isinstance(expected, dict):
                visit(existing, expected, here)
            else:
                existing_leaf_count += 1

        # Existing local keys are never dropped, renamed or echoed.
        extra_local_keys += len(old.keys() - new.keys())

    visit(current, template)
    return {
        'status': 'review_required' if (
            candidates or protected_missing or unapproved_missing
            or type_conflicts or extra_local_keys) else 'compatible',
        'read_only': True,
        'changes_applied': False,
        'apply_supported': False,
        'existing_values_compared': False,
        'existing_keys_preserved_by_proposal': True,
        'existing_leaf_keys_count': existing_leaf_count,
        'candidates_for_explicit_review': sorted(candidates),
        'protected_missing_count': protected_missing,
        'unapproved_missing_count': unapproved_missing,
        'type_conflicts': sorted(type_conflicts, key=lambda c: c['key']),
        'extra_local_keys_count': extra_local_keys,
        'requires_explicit_approval': bool(
            candidates or protected_missing or unapproved_missing
            or type_conflicts or extra_local_keys),
    }
