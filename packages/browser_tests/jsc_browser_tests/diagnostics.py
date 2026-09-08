"""Keep browser diagnostics useful without persisting session credentials."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SECRET_KEY = re.compile(r"^(sid|access_?token|refresh_?token|oauth_token|authorization|cookie|password|_?confirmationtoken|csrf_?token|csrf|nonce)$", re.I)
_QUERY_SECRET = re.compile(
    r"(?i)((?:sid|access_?token|refresh_?token|oauth_token|password|_?confirmationtoken|csrf_?token|csrf|nonce)\s*(?:=|%(?:25)*3d)\s*)[^\s&\"'<>]+"
)
_HEADER_SECRET = re.compile(r"(?im)\b(authorization|cookie)\s*[:=]\s*[^\r\n]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"'<>]+")


def redact(value):
    """Redact known credential fields/patterns recursively at output boundaries."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SECRET_KEY.fullmatch(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    value = _HEADER_SECRET.sub(lambda m: m.group(1) + ": [REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _QUERY_SECRET.sub(lambda m: m.group(1) + "[REDACTED]", value)


def exception_detail(exc: Exception) -> str:
    return redact(f"{type(exc).__name__}: {exc}")


def artifact_component(value: str) -> str:
    """Encode a label as one stable, collision-resistant path component."""
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:64] or "unnamed"
    return f"{label}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def artifact_child(root: Path, *labels: str) -> Path:
    base = root.resolve()
    child = base.joinpath(*(artifact_component(label) for label in labels))
    if not child.resolve().is_relative_to(base):
        raise ValueError("Browser artifact path escapes the selected workspace")
    return child
