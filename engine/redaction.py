from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:^|[_-])(api[_-]?key|token|secret|password|authorization|cookie|credential)(?:$|[_-])", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret|password)=)[^&\s]+")
_KV_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|access[_-]?token|secret|password)\s*[:=]\s*[^,;\s]+")


def sanitize(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            if _SENSITIVE_KEY.search(key):
                out[key] = "[REDACTED]"
            else:
                out[key] = sanitize(v, key)
        return out
    if isinstance(value, list):
        return [sanitize(v, key_hint) for v in value]
    if isinstance(value, tuple):
        return [sanitize(v, key_hint) for v in value]
    if isinstance(value, str):
        text = _BEARER.sub("Bearer [REDACTED]", value)
        text = _QUERY_SECRET.sub(lambda m: m.group(1) + "[REDACTED]", text)
        text = _KV_SECRET.sub(lambda m: m.group(1) + "=[REDACTED]", text)
        return text
    return value
