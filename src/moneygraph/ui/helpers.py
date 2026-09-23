"""Pure presentation helpers shared by Streamlit pages and tests."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


def parse_gid_list(value: str, *, max_items: int = 20) -> list[str]:
    """Parse opaque GIDs without coercing numeric-looking values to integers."""

    result: list[str] = []
    for part in re.split(r"[,;|\s]+", value):
        gid = part.strip()
        if gid and gid not in result:
            result.append(gid)
        if len(result) >= max_items:
            break
    return result


def format_kzt(value: float | int | None) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}".replace(".", ",") + " млрд ₸"
    if absolute >= 1_000_000:
        return f"{number / 1_000_000:.2f}".replace(".", ",") + " млн ₸"
    return f"{number:,.0f}".replace(",", " ") + " ₸"


def format_period(value: Any) -> str:
    if isinstance(value, Mapping):
        start, end = value.get("start"), value.get("end")
        if start and end:
            return f"{start} — {end}"
        return str(start or end or "—")
    return str(value) if value else "—"


def as_records(payload: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    """Normalize plain, enveloped and paginated list responses."""

    current = payload
    if isinstance(current, Mapping) and "data" in current:
        current = current["data"]
    if isinstance(current, list):
        return [dict(item) for item in current if isinstance(item, Mapping)]
    if not isinstance(current, Mapping):
        return []
    for key in (*preferred_keys, "items", "results", "nodes", "clusters", "investigations"):
        value = current.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def first_value(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def bool_from_env(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
