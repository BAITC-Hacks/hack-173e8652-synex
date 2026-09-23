"""Observed temporal activity and explainable FIFO flow matching."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FIFOMetrics:
    matched_volume: float
    unmatched_out_volume: float
    observed_out_volume: float
    matched_out_ratio: float
    fast_forward_0_2d_ratio: float
    median_holding_days: float
    weighted_holding_days: float
    matched_chunk_count: int


def _prepare_transactions(transactions: pd.DataFrame) -> pd.DataFrame:
    required = {"src", "dst", "date", "sum_kzt"}
    if missing := required.difference(transactions.columns):
        raise ValueError(f"transactions missing required columns: {', '.join(sorted(missing))}")
    frame = transactions[["src", "dst", "date", "sum_kzt"]].copy(deep=True)
    frame["src"] = frame["src"].astype("string")
    frame["dst"] = frame["dst"].astype("string")
    try:
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"transactions.date contains invalid values: {exc}") from exc
    frame["sum_kzt"] = pd.to_numeric(frame["sum_kzt"], errors="raise").astype(float)
    if frame[["src", "dst", "date", "sum_kzt"]].isna().any().any():
        raise ValueError("transactions contains null values required for temporal analysis")
    if (frame["sum_kzt"] <= 0).any():
        raise ValueError("transactions.sum_kzt must be positive for temporal analysis")
    frame["_order"] = np.arange(len(frame), dtype=np.int64)
    return frame


def _weighted_median(values: list[tuple[float, float]]) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in ordered)
    midpoint = total_weight / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= midpoint:
            return float(value)
    return float(ordered[-1][0])


def fifo_match_node(gid: str | int, transactions: pd.DataFrame) -> FIFOMetrics:
    """Match outgoing volume only against same-day or earlier observed inflows.

    The source has day granularity and no intra-day order. To make the estimate
    deterministic, observed inflows are made available before outflows on the same
    date. Future dates are never used.
    """

    frame = _prepare_transactions(transactions)
    node = str(gid)
    relevant = frame[(frame["src"] == node) | (frame["dst"] == node)]
    events: list[tuple[pd.Timestamp, int, int, float]] = []
    for raw_row in relevant.to_dict(orient="records"):
        row = cast(dict[str, Any], raw_row)
        event_date = pd.Timestamp(row["date"])
        order = int(row["_order"])
        amount = float(row["sum_kzt"])
        if str(row["dst"]) == node:
            events.append((event_date, 0, order, amount))
        if str(row["src"]) == node:
            events.append((event_date, 1, order, amount))
    events.sort(key=lambda event: (event[0], event[1], event[2]))

    inflow_lots: deque[tuple[pd.Timestamp, float]] = deque()
    matched_chunks: list[tuple[float, float]] = []
    observed_out = 0.0
    unmatched_out = 0.0
    for event_date, direction, _, amount in events:
        if direction == 0:
            inflow_lots.append((event_date, amount))
            continue
        observed_out += amount
        remaining = amount
        while remaining > 1e-9 and inflow_lots:
            lot_date, lot_remaining = inflow_lots[0]
            consumed = min(remaining, lot_remaining)
            holding_days = max((event_date - lot_date).total_seconds() / 86_400.0, 0.0)
            matched_chunks.append((holding_days, consumed))
            remaining -= consumed
            lot_remaining -= consumed
            if lot_remaining <= 1e-9:
                inflow_lots.popleft()
            else:
                inflow_lots[0] = (lot_date, lot_remaining)
        unmatched_out += max(remaining, 0.0)

    matched = sum(volume for _, volume in matched_chunks)
    fast = sum(volume for days, volume in matched_chunks if days <= 2.0)
    matched_ratio = matched / observed_out if observed_out > 0 else 0.0
    fast_ratio = fast / matched if matched > 0 else 0.0
    weighted_holding = (
        sum(days * volume for days, volume in matched_chunks) / matched
        if matched > 0
        else float("nan")
    )
    return FIFOMetrics(
        matched_volume=float(matched),
        unmatched_out_volume=float(unmatched_out),
        observed_out_volume=float(observed_out),
        matched_out_ratio=float(matched_ratio),
        fast_forward_0_2d_ratio=float(fast_ratio),
        median_holding_days=_weighted_median(matched_chunks),
        weighted_holding_days=float(weighted_holding),
        matched_chunk_count=len(matched_chunks),
    )


def _daily_side_metrics(
    frame: pd.DataFrame,
    *,
    gid_column: str,
    counterparty_column: str,
    prefix: str,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "gid",
                f"first_{prefix}_date",
                f"last_{prefix}_date",
                f"active_{prefix}_days",
                f"max_daily_unique_{'payers' if prefix == 'incoming' else 'recipients'}",
                f"{prefix}_daily_spike_ratio",
            ]
        )
    working = frame.copy(deep=True)
    working["day"] = working["date"].dt.normalize()
    daily = (
        working.groupby([gid_column, "day"], sort=True)
        .agg(daily_kzt=("sum_kzt", "sum"), unique_counterparties=(counterparty_column, "nunique"))
        .reset_index()
    )
    counterpart_label = "payers" if prefix == "incoming" else "recipients"
    rows: list[dict[str, object]] = []
    for gid, group in daily.groupby(gid_column, sort=True):
        median_daily = float(group["daily_kzt"].median())
        rows.append(
            {
                "gid": str(gid),
                f"first_{prefix}_date": group["day"].min(),
                f"last_{prefix}_date": group["day"].max(),
                f"active_{prefix}_days": len(group),
                f"max_daily_unique_{counterpart_label}": int(group["unique_counterparties"].max()),
                f"{prefix}_daily_spike_ratio": (
                    float(group["daily_kzt"].max()) / median_daily if median_daily > 0 else 0.0
                ),
            }
        )
    return pd.DataFrame(rows)


def compute_temporal_features(nodes: pd.DataFrame, transactions: pd.DataFrame) -> pd.DataFrame:
    """Compute daily activity and FIFO metrics for every node, including isolates."""

    required_nodes = {"gid", "is_seed"}
    if missing := required_nodes.difference(nodes.columns):
        raise ValueError(f"nodes missing required columns: {', '.join(sorted(missing))}")
    node_frame = nodes[["gid", "is_seed"]].copy(deep=True)
    node_frame["gid"] = node_frame["gid"].astype("string")
    node_frame = node_frame.sort_values("gid", kind="stable").reset_index(drop=True)
    frame = _prepare_transactions(transactions)

    incoming = _daily_side_metrics(
        frame, gid_column="dst", counterparty_column="src", prefix="incoming"
    )
    outgoing = _daily_side_metrics(
        frame, gid_column="src", counterparty_column="dst", prefix="outgoing"
    )
    result = node_frame.merge(incoming, on="gid", how="left", validate="one_to_one")
    result = result.merge(outgoing, on="gid", how="left", validate="one_to_one")

    fifo_rows: list[dict[str, float | int | str]] = []
    grouped: dict[str, pd.DataFrame] = {
        str(gid): group.copy(deep=True)
        for gid, group in pd.concat(
            [
                frame.assign(_node=frame["src"]),
                frame.loc[frame["dst"] != frame["src"]].assign(_node=frame["dst"]),
            ],
            ignore_index=True,
        ).groupby("_node", sort=False)
    }
    for gid in result["gid"]:
        relevant = grouped.get(str(gid), frame.iloc[0:0])
        metrics = fifo_match_node(str(gid), relevant)
        fifo_rows.append(
            {
                "gid": str(gid),
                "matched_volume": metrics.matched_volume,
                "unmatched_out_volume": metrics.unmatched_out_volume,
                "observed_out_volume": metrics.observed_out_volume,
                "matched_out_ratio": metrics.matched_out_ratio,
                "fast_forward_0_2d_ratio": metrics.fast_forward_0_2d_ratio,
                "median_holding_days": metrics.median_holding_days,
                "weighted_holding_days": metrics.weighted_holding_days,
                "matched_chunk_count": metrics.matched_chunk_count,
            }
        )
    result = result.merge(pd.DataFrame(fifo_rows), on="gid", how="left", validate="one_to_one")

    count_columns = [
        "active_incoming_days",
        "active_outgoing_days",
        "max_daily_unique_payers",
        "max_daily_unique_recipients",
        "matched_chunk_count",
    ]
    result[count_columns] = result[count_columns].fillna(0).astype(int)
    numeric_columns = [
        "incoming_daily_spike_ratio",
        "outgoing_daily_spike_ratio",
        "matched_volume",
        "unmatched_out_volume",
        "observed_out_volume",
        "matched_out_ratio",
        "fast_forward_0_2d_ratio",
    ]
    result[numeric_columns] = result[numeric_columns].fillna(0.0)
    # Compact aliases match the terms used throughout the scoring/reporting layers.
    result["active_in_days"] = result["active_incoming_days"]
    result["active_out_days"] = result["active_outgoing_days"]
    result["in_daily_spike_ratio"] = result["incoming_daily_spike_ratio"]
    result["out_daily_spike_ratio"] = result["outgoing_daily_spike_ratio"]
    result["first_in_date"] = result["first_incoming_date"]
    result["last_in_date"] = result["last_incoming_date"]
    result["first_out_date"] = result["first_outgoing_date"]
    result["last_out_date"] = result["last_outgoing_date"]

    incoming_volume = frame.groupby("dst", sort=True)["sum_kzt"].sum()
    has_observed_incoming = result["gid"].map(incoming_volume).fillna(0.0).gt(0)
    result["temporal_observation_reliable"] = (~result["is_seed"]) & has_observed_incoming
    result["temporal_confidence"] = np.select(
        [result["temporal_observation_reliable"], result["is_seed"] & has_observed_incoming],
        [1.0, 0.35],
        default=0.0,
    )
    return result.drop(columns="is_seed").sort_values("gid", kind="stable").reset_index(drop=True)
