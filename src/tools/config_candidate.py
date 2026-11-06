"""OPS-01 offline-only TOML candidate generator.

No filesystem I/O, service calls, implicit apply, or TOML reserialization.
Only allowlisted new scalar keys in *existing, simple* table sections.
The original content must survive byte-for-byte except for inserted lines.
"""
from __future__ import annotations

import copy
import hashlib
import re
import tomllib

from .config_migration_plan import APPROVED_DEFAULTS, _protected, plan_migration


class CandidateRejected(ValueError):
    """No candidate may be produced from an ambiguous or unsafe input."""


_SECTION = re.compile(r"^\s*\[([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)\]\s*(?:#.*)?$")


def _load(raw):
    if not isinstance(raw, bytes) or not raw or b"\r" in raw or b"\x00" in raw:
        raise CandidateRejected("unsupported TOML encoding")
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CandidateRejected("invalid TOML") from exc


def _sections(raw):
    """Locate unambiguous, explicitly declared table blocks."""
    locations = {}
    lines = raw.decode("utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        bare = line.rstrip("\n")
        if bare.lstrip().startswith("["):
            match = _SECTION.fullmatch(bare)
            if match is None:
                # Array-of-tables and quoted/dotted variants need a TOML-aware editor.
                raise CandidateRejected("unsupported table syntax")
            name = tuple(match.group(1).split("."))
            if name in locations:
                raise CandidateRejected("ambiguous table declaration")
            locations[name] = index
    return lines, locations


def build_candidate(current_bytes: bytes, template_bytes: bytes,
                    selected_keys: tuple[str, ...]):
    """Return (candidate_bytes, original_sha256), WITHOUT writing anything.

    Callers must separately authorize any real write. This is an offline
    building block, *not* an apply or rollback API.
    """
    current = _load(current_bytes)
    template = _load(template_bytes)
    if not isinstance(selected_keys, tuple) or not selected_keys:
        raise CandidateRejected("explicit nonempty selection required")
    if len(set(selected_keys)) != len(selected_keys):
        raise CandidateRejected("duplicate selection")
    proposal = plan_migration(current, template)
    if proposal["type_conflicts"]:
        raise CandidateRejected("conflicting configuration types")
    allowed = set(proposal["candidates_for_explicit_review"])
    if any(not isinstance(key, str) or key not in allowed for key in selected_keys):
        raise CandidateRejected("selection not approved for proposal")

    lines, sections = _sections(current_bytes)
    _sections(template_bytes)
    additions = {}
    expected = copy.deepcopy(current)

    for key in selected_keys:
        parts = tuple(key.split("."))
        if parts not in APPROVED_DEFAULTS or _protected(parts):
            raise CandidateRejected("protected or unreviewed key")
        table_path, leaf = parts[:-1], parts[-1]
        if table_path not in sections:
            raise CandidateRejected("target table is not explicitly declared")
        dest = expected
        source = template
        for part in table_path:
            if not isinstance(dest, dict) or part not in dest:
                raise CandidateRejected("missing destination table")
            dest = dest[part]
            source = source[part]
        value = APPROVED_DEFAULTS[parts]
        if not isinstance(dest, dict) or leaf in dest or source[leaf] != value:
            raise CandidateRejected("candidate is not a missing audited default")
        dest[leaf] = value
        # The allowlist is integers only, not bools.
        if type(value) is not int:
            raise CandidateRejected("unsupported scalar")
        additions.setdefault(table_path, []).append(f"{leaf} = {value}\n")

    for table_path, new_lines in sorted(additions.items(),
                                         key=lambda item: sections[item[0]], reverse=True):
        start = sections[table_path]
        following = [index for index in sections.values() if index > start]
        end = min(following) if following else len(lines)
        # Avoid attaching new text to a line without a terminating newline.
        if end and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines[end:end] = sorted(new_lines)

    candidate = "".join(lines).encode("utf-8")
    if _load(candidate) != expected:
        raise CandidateRejected("candidate verification failed")
    return candidate, hashlib.sha256(current_bytes).hexdigest()
