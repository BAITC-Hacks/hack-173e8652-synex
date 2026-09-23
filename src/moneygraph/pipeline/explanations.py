"""Short, factual Russian-language explanations for deterministic outputs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd


def _get(row: Mapping[str, Any] | pd.Series, name: str, default: Any = 0) -> Any:
    value = row.get(name, default)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return value


def _number(row: Mapping[str, Any] | pd.Series, name: str, default: float = 0.0) -> float:
    try:
        value = float(_get(row, name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _money(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f} млрд KZT"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн KZT"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f} тыс. KZT"
    return f"{value:.0f} KZT"


def _bounded(text: str, limit: int = 200) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip(" ,;:.") + "…"


def build_evidence(row: Mapping[str, Any] | pd.Series) -> str:
    """Build <=200 character evidence from observed numeric facts only."""

    role = str(_get(row, "role", "peripheral"))
    in_deg = int(_number(row, "in_deg"))
    out_deg = int(_number(row, "out_deg"))
    in_kzt = _number(row, "in_kzt")
    out_kzt = _number(row, "out_kzt")
    is_seed = bool(_get(row, "is_seed", False))
    truncated = bool(_get(row, "truncated_by_depth", False))

    if truncated:
        text = (
            f"Граница выборки: depth=4, входов {in_deg}, исходящих {out_deg}; "
            "роль terminal не подтверждена."
        )
    elif in_deg == 0 and out_deg == 0:
        text = "Наблюдаемых связей 0, оборот 0 KZT; специальных структурных признаков недостаточно."
    elif role == "consolidator":
        forwarded = out_kzt / in_kzt if in_kzt > 0.0 else 0.0
        text = (
            f"Признаки консолидации: {in_deg} плательщиков, вход {_money(in_kzt)}, "
            f"далее ушло {forwarded:.0%} наблюдаемого объёма."
        )
    elif role == "distributor":
        out_tx = int(_number(row, "out_tx"))
        text = (
            f"Признаки распределения: {out_deg} получателей, {out_tx} исходящих операций, "
            f"объём {_money(out_kzt)}."
        )
    elif role == "transit":
        matched = _number(row, "matched_out_ratio")
        fast = _number(row, "fast_forward_0_2d_ratio")
        holding = _number(row, "median_holding_days")
        text = (
            f"Признаки транзита: сопоставлено {matched:.0%}, за 0-2 дня {fast:.0%}, "
            f"медиана удержания {holding:.1f} дня."
        )
    elif role == "terminal":
        forwarded = out_kzt / in_kzt if in_kzt > 0.0 else 0.0
        text = (
            f"В наблюдаемой сети: {in_deg} входящих связей, вход {_money(in_kzt)}, "
            f"далее ушло {forwarded:.0%}."
        )
    elif role == "coordinator":
        betweenness = _number(row, "betweenness_percentile", _number(row, "betweenness"))
        seed_reach = int(_number(row, "seed_reach_count"))
        bridge = _number(row, "cross_cluster_bridge_signal")
        text = (
            f"Структурное посредничество: percentile {betweenness:.2f}, достигнуто seed "
            f"{seed_reach}, межкластерный сигнал {bridge:.2f}."
        )
    else:
        text = (
            f"Связи: входящих {in_deg}, исходящих {out_deg}, оборот "
            f"{_money(in_kzt + out_kzt)}; специальных признаков недостаточно."
        )

    if is_seed:
        text = text.rstrip(".") + "; seed=1, наблюдаемый входящий поток неполон."
    return _bounded(text)


_DRIVER_LABELS: dict[str, str] = {
    "priority_coordinator": "посредничество",
    "priority_consolidator": "консолидация",
    "priority_distributor": "распределение",
    "priority_transit": "транзит",
    "priority_pagerank": "PageRank",
    "priority_betweenness": "betweenness",
    "priority_seed_reach": "связь с seed",
    "priority_turnover": "наблюдаемый оборот",
}


def build_priority_explanation(row: Mapping[str, Any] | pd.Series) -> str:
    """Explain review ordering without interpreting it as crime probability."""

    priority = _number(row, "priority_score")
    contributions = [
        (_number(row, column), label)
        for column, label in _DRIVER_LABELS.items()
        if _number(row, column) > 0.0
    ]
    contributions.sort(key=lambda item: (-item[0], item[1]))
    drivers = ", ".join(f"{label} +{value:.2f}" for value, label in contributions[:3])
    if not drivers:
        role = str(_get(row, "role", "не определена"))
        role_score = _number(row, "role_score")
        drivers = f"роль {role} {role_score:.2f}"
    return _bounded(f"Приоритет проверки {priority:.2f}: {drivers}.", limit=320)
