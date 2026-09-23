"""Read the three case datasets without mutating source files or caller frames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATASET_FILES = {
    "nodes": "nodes.parquet",
    "edges": "edges.parquet",
    "transactions": "transactions.parquet",
}


class DataLoadError(ValueError):
    """Raised when an input file cannot be decoded into the expected table."""


@dataclass(frozen=True, slots=True)
class DatasetBundle:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    transactions: pd.DataFrame


def _canonicalize_ids(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    result = frame.copy(deep=True)
    for column in columns:
        if column in result.columns:
            # IDs are opaque labels. StringDtype preserves 18-digit int64 values exactly
            # and avoids precision loss when results are later consumed by JavaScript.
            result[column] = result[column].astype("string")
    return result


def load_dataset(data_dir: str | Path) -> DatasetBundle:
    """Load and normalize case tables, failing explicitly on missing/invalid input."""

    root = Path(data_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Data directory not found: {root}")
    missing = [filename for filename in DATASET_FILES.values() if not (root / filename).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing required data files in {root}: {', '.join(sorted(missing))}"
        )

    try:
        nodes = pd.read_parquet(root / DATASET_FILES["nodes"])
        edges = pd.read_parquet(root / DATASET_FILES["edges"])
        transactions = pd.read_parquet(root / DATASET_FILES["transactions"])
    except Exception as exc:  # pyarrow exposes several backend-specific exception types
        raise DataLoadError(f"Failed to read Parquet dataset from {root}: {exc}") from exc

    nodes = _canonicalize_ids(nodes, ("gid",))
    edges = _canonicalize_ids(edges, ("src", "dst"))
    transactions = _canonicalize_ids(transactions, ("src", "dst"))
    if "date" in transactions.columns:
        try:
            transactions["date"] = pd.to_datetime(transactions["date"], errors="raise")
        except (TypeError, ValueError) as exc:
            raise DataLoadError(f"transactions.date cannot be parsed as dates: {exc}") from exc

    return DatasetBundle(nodes=nodes, edges=edges, transactions=transactions)
