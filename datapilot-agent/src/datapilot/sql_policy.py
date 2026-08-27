from __future__ import annotations

import re

from .models import PolicyDecision

_blocked = re.compile(r"\b(insert|update|delete|drop|alter|attach|copy|install|load|export|create|pragma)\b|;|read_csv|read_parquet|httpfs|glob\s*\(", re.I)


def validate_sql(sql: str) -> PolicyDecision:
    cleaned = sql.strip()
    if not cleaned:
        return PolicyDecision(allowed=False, reason="empty SQL")
    if _blocked.search(cleaned):
        return PolicyDecision(allowed=False, reason="only one read-only SELECT statement is allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        return PolicyDecision(allowed=False, reason="query must start with SELECT or WITH")
    return PolicyDecision(allowed=True, reason="allowed read-only query")
