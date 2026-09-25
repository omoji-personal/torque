"""Keep browser diagnostics useful without persisting session credentials."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SECRET_KEY = re.compile(r"^(sid|session_?id|frontdoor_?url|access_?token|refresh_?token|oauth_token|authorization|cookie|password|_?confirmationtoken|csrf_?token|csrf|nonce)$", re.I)
_QUERY_SECRET = re.compile(
    r"(?i)((?:sid|session_?id|access_?token|refresh_?token|oauth_token|password|_?confirmationtoken|csrf_?token|csrf|nonce)\s*(?:[=:]|%(?:25)*3d)\s*)[^\s&\"'<>]+"
)
# A JSON or Python-repr credential field ("accessToken": "..." as sf org display prints it,
# or 'accessToken': '...' from a dict's repr).
_JSON_SECRET = re.compile(
    r'(?i)((["\'])(?:sid|session_?id|access_?token|refresh_?token|oauth_?token|password|auth_?code|sfdx_?auth_?url)\2'
    r'\s*:\s*)(["\'])(?:(?!\3)[^\\]|\\.)*\3')
# A Salesforce session token by its shape: the org ID, "!" (or %21, %2521) and the token.
_SF_TOKEN = re.compile(r"00D[A-Za-z0-9]{12,15}(?:!|%(?:25)*21)[A-Za-z0-9._\-]+")
_HEADER_SECRET = re.compile(r"(?im)\b(authorization|cookie)\s*[:=]\s*[^\r\n]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"'<>]+")
# A session URL is removed whole, never left as a live-looking URL with one value masked:
# any http(s) or ws(s) URL (its scheme written plainly or URL-encoded) that goes through
# frontdoor or secur/, or carries a sid parameter anywhere, a URL-encoded one inside
# retURL included; and any frontdoor.jsp reference without a scheme.
_URL_CHARS = r"[^\s\"'<>]"
_SID_PARAM = r"(?:[?&;#]|%(?:25)*(?:3f|26|3b|23))sid(?:=|%(?:25)*3d)"
_SESSION_URL = re.compile(
    rf"(?i)(?:https?|wss?)(?::|%(?:25)*3a)(?://|%(?:25)*2f%(?:25)*2f){_URL_CHARS}*?"
    rf"(?:frontdoor\.jsp|secur(?:/|%(?:25)*2f)|{_SID_PARAM}){_URL_CHARS}*")
_TOKEN = re.compile(rf"{_URL_CHARS}+")
SESSION_URL_MARK = "[redacted session URL]"


def redact(value):
    """Redact known credential fields/patterns recursively at output boundaries."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SECRET_KEY.fullmatch(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    value = _SESSION_URL.sub(SESSION_URL_MARK, value)
    if "frontdoor.jsp" in value.casefold():
        value = _TOKEN.sub(lambda m: SESSION_URL_MARK if "frontdoor.jsp" in m.group().casefold() else m.group(), value)
    value = _JSON_SECRET.sub(lambda m: m.group(1) + m.group(3) + "[REDACTED]" + m.group(3), value)
    value = _SF_TOKEN.sub("[REDACTED]", value)
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
