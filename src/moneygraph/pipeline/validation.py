"""Strict, explainable validation for the MoneyGraph case tables."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from moneygraph.pipeline.ingestion import DatasetBundle

REQUIRED_COLUMNS = {
    "nodes": frozenset({"gid", "depth", "is_seed"}),
    "edges": frozenset({"src", "dst", "sum_kzt", "n_tx", "depth"}),
    "transactions": frozenset({"src", "dst", "date", "sum_kzt"}),
}


class DataValidationError(ValueError):
    """Raised when one or more critical data invariants are violated."""

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(errors)
        super().__init__("Data validation failed: " + "; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class ValidationReport:
    is_valid: bool
    counts: dict[str, int]
    isolated_gids: tuple[str, ...]
    self_loops: int
    weakly_connected_components: int
    min_date: str | None
    max_date: str | None
    total_kzt: float
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _missing_columns(name: str, frame: pd.DataFrame) -> set[str]:
    return set(REQUIRED_COLUMNS[name]).difference(frame.columns)


def _check_numeric(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    table: str,
    integer: bool = False,
) -> list[str]:
    errors: list[str] = []
    for column in columns:
        if column not in frame:
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            errors.append(f"{table}.{column} must be numeric")
        elif integer and not pd.api.types.is_integer_dtype(frame[column]):
            errors.append(f"{table}.{column} must be integer")
    return errors


def _canonical_ids(series: pd.Series) -> pd.Series:
    return series.astype("string")


def validate_dataset(
    bundle: DatasetBundle,
    *,
    expected_start: str | date | pd.Timestamp = "2026-07-01",
    expected_end: str | date | pd.Timestamp = "2026-07-31",
    amount_atol: float = 0.01,
) -> ValidationReport:
    """Validate schema, semantics, aggregation, and graph consistency.

    The function operates on deep copies and never repairs source data silently.
    All critical findings are returned together in :class:`DataValidationError`.
    """

    nodes = bundle.nodes.copy(deep=True)
    edges = bundle.edges.copy(deep=True)
    transactions = bundle.transactions.copy(deep=True)
    frames = {"nodes": nodes, "edges": edges, "transactions": transactions}
    errors: list[str] = []
    warnings: list[str] = []

    for name, frame in frames.items():
        missing = _missing_columns(name, frame)
        if missing:
            errors.append(f"{name} missing required columns: {', '.join(sorted(missing))}")
    if errors:
        raise DataValidationError(errors)

    errors.extend(_check_numeric(nodes, ("depth",), table="nodes", integer=True))
    errors.extend(_check_numeric(edges, ("sum_kzt",), table="edges"))
    errors.extend(_check_numeric(edges, ("n_tx", "depth"), table="edges", integer=True))
    errors.extend(_check_numeric(transactions, ("sum_kzt",), table="transactions"))
    if not pd.api.types.is_bool_dtype(nodes["is_seed"]):
        errors.append("nodes.is_seed must be boolean")

    for name, frame, columns in (
        ("nodes", nodes, ("gid", "depth", "is_seed")),
        ("edges", edges, ("src", "dst", "sum_kzt", "n_tx", "depth")),
        ("transactions", transactions, ("src", "dst", "date", "sum_kzt")),
    ):
        null_columns = [column for column in columns if frame[column].isna().any()]
        if null_columns:
            errors.append(f"{name} contains nulls in: {', '.join(null_columns)}")

    if nodes["gid"].duplicated().any():
        errors.append("nodes.gid must be unique")
    if edges.duplicated(["src", "dst"]).any():
        errors.append("edges src,dst pairs must be unique")
    if pd.api.types.is_numeric_dtype(edges["sum_kzt"]) and (edges["sum_kzt"] <= 0).any():
        errors.append("edges.sum_kzt must be positive")
    if pd.api.types.is_numeric_dtype(edges["n_tx"]) and (edges["n_tx"] <= 0).any():
        errors.append("edges.n_tx must be positive")
    if (
        pd.api.types.is_numeric_dtype(transactions["sum_kzt"])
        and (transactions["sum_kzt"] <= 0).any()
    ):
        errors.append("transactions.sum_kzt must be positive")
    if pd.api.types.is_numeric_dtype(nodes["depth"]) and not nodes["depth"].between(0, 4).all():
        errors.append("nodes.depth must be between 0 and 4")
    if pd.api.types.is_numeric_dtype(edges["depth"]) and not edges["depth"].between(0, 4).all():
        errors.append("edges.depth must be between 0 and 4")

    try:
        parsed_dates = pd.to_datetime(transactions["date"], errors="raise")
    except (TypeError, ValueError) as exc:
        errors.append(f"transactions.date contains invalid dates: {exc}")
        parsed_dates = pd.Series(pd.NaT, index=transactions.index, dtype="datetime64[ns]")
    if parsed_dates.notna().any():
        start = pd.Timestamp(expected_start)
        end = pd.Timestamp(expected_end)
        if ((parsed_dates < start) | (parsed_dates > end)).any():
            errors.append(
                f"transactions.date must be within {start.date().isoformat()} and "
                f"{end.date().isoformat()}"
            )

    node_ids = set(_canonical_ids(nodes["gid"]).dropna())
    edge_src = _canonical_ids(edges["src"])
    edge_dst = _canonical_ids(edges["dst"])
    tx_src = _canonical_ids(transactions["src"])
    tx_dst = _canonical_ids(transactions["dst"])
    unknown_edges = (set(edge_src.dropna()) | set(edge_dst.dropna())).difference(node_ids)
    unknown_transactions = (set(tx_src.dropna()) | set(tx_dst.dropna())).difference(node_ids)
    if unknown_edges:
        errors.append(f"edges reference {len(unknown_edges)} gids absent from nodes")
    if unknown_transactions:
        errors.append(f"transactions reference {len(unknown_transactions)} gids absent from nodes")

    self_loops = int((edge_src == edge_dst).sum())
    if self_loops:
        warnings.append(f"Detected {self_loops} self-loop edge(s); retained for analysis")

    # Compare the source aggregation after normalizing only the opaque identifier columns.
    comparable_edges = edges.assign(src=edge_src, dst=edge_dst)
    comparable_tx = transactions.assign(src=tx_src, dst=tx_dst)
    if not errors or all("numeric" not in error for error in errors):
        aggregated = (
            comparable_tx.groupby(["src", "dst"], sort=True, dropna=False)
            .agg(tx_sum_kzt=("sum_kzt", "sum"), tx_n_tx=("sum_kzt", "size"))
            .reset_index()
        )
        comparison = comparable_edges[["src", "dst", "sum_kzt", "n_tx"]].merge(
            aggregated,
            on=["src", "dst"],
            how="outer",
            indicator=True,
        )
        same_pairs = comparison["_merge"].eq("both")
        same_amounts = np.isclose(
            comparison["sum_kzt"].fillna(np.inf),
            comparison["tx_sum_kzt"].fillna(-np.inf),
            rtol=1e-9,
            atol=amount_atol,
        )
        same_counts = comparison["n_tx"].fillna(-1).eq(comparison["tx_n_tx"].fillna(-2))
        if not (same_pairs & same_amounts & same_counts).all():
            errors.append(
                "edges aggregation does not match transactions by pair, amount, and count"
            )

    if errors:
        raise DataValidationError(errors)

    edge_node_ids = set(edge_src) | set(edge_dst)
    isolated = tuple(sorted(node_ids.difference(edge_node_ids)))
    if isolated:
        warnings.append(f"Detected {len(isolated)} isolated node(s); retained in all outputs")

    graph = nx.Graph()
    graph.add_nodes_from(sorted(node_ids))
    graph.add_edges_from(zip(edge_src.tolist(), edge_dst.tolist(), strict=True))
    weak_components = nx.number_connected_components(graph) if graph.number_of_nodes() else 0
    min_date = parsed_dates.min().date().isoformat() if parsed_dates.notna().any() else None
    max_date = parsed_dates.max().date().isoformat() if parsed_dates.notna().any() else None

    return ValidationReport(
        is_valid=True,
        counts={
            "nodes": len(nodes),
            "edges": len(edges),
            "transactions": len(transactions),
            "seed": int(nodes["is_seed"].sum()),
        },
        isolated_gids=isolated,
        self_loops=self_loops,
        weakly_connected_components=weak_components,
        min_date=min_date,
        max_date=max_date,
        total_kzt=float(edges["sum_kzt"].sum()),
        warnings=tuple(warnings),
    )
