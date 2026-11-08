"""Pure, immutable UI notice data. No GPIO, network or player dependencies."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

SEVERITIES = frozenset(("info", "success", "warning", "error"))


@dataclass(frozen=True)
class Notice:
    key: str
    severity: str = "info"
    params: Mapping[str, object] = field(default_factory=dict)
    priority: int = 0
    dismissible: bool = True
    duration_seconds: float | None = None

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("notice key must be a nonempty string")
        if self.severity not in SEVERITIES:
            raise ValueError("unknown notice severity")
        if type(self.priority) is not int:
            raise ValueError("notice priority must be an integer")
        if type(self.dismissible) is not bool:
            raise ValueError("dismissible must be boolean")
        if self.duration_seconds is not None:
            if (isinstance(self.duration_seconds, bool)
                    or not isinstance(self.duration_seconds, (int, float))
                    or not 0 <= self.duration_seconds < float("inf")):
                raise ValueError("duration_seconds must be finite and nonnegative")
        if not isinstance(self.params, Mapping):
            raise ValueError("notice params must be a mapping")
        values = dict(self.params)
        if any(not isinstance(k, str) for k in values):
            raise ValueError("notice parameter names must be strings")
        object.__setattr__(self, "params", MappingProxyType(values))


def notice_text(notice: Notice, language="de") -> str:
    """Resolve through existing locale catalogs and English fallback.

    Unknown keys and malformed/missing parameters intentionally raise rather
    than silently displaying an untranslated or misleading warning.
    """
    from .i18n import Translator
    return Translator(language).tr(notice.key, **notice.params)
