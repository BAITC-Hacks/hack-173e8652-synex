from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def synthetic_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Small directed graph with a seed, a chain, a sink, and an isolate."""
    nodes = pd.DataFrame(
        [
            {"gid": "seed", "depth": 0, "is_seed": True},
            {"gid": "collector", "depth": 1, "is_seed": False},
            {"gid": "bridge", "depth": 2, "is_seed": False},
            {"gid": "sink", "depth": 3, "is_seed": False},
            {"gid": "truncated", "depth": 4, "is_seed": False},
            {"gid": "isolated", "depth": 0, "is_seed": True},
        ]
    )
    tx = pd.DataFrame(
        [
            {"src": "seed", "dst": "collector", "date": "2026-07-01", "sum_kzt": 10_000.0},
            {"src": "seed", "dst": "collector", "date": "2026-07-02", "sum_kzt": 20_000.0},
            {"src": "collector", "dst": "bridge", "date": "2026-07-03", "sum_kzt": 25_000.0},
            {"src": "bridge", "dst": "sink", "date": "2026-07-04", "sum_kzt": 15_000.0},
            {"src": "bridge", "dst": "truncated", "date": "2026-07-05", "sum_kzt": 8_000.0},
        ]
    )
    tx["date"] = pd.to_datetime(tx["date"])
    edges = (
        tx.groupby(["src", "dst"], sort=True)
        .agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        .reset_index()
    )
    depth_by_gid = nodes.set_index("gid")["depth"]
    edges["depth"] = edges["dst"].map(depth_by_gid).astype(int)
    return nodes, edges, tx


@pytest.fixture
def synthetic_data_dir(
    tmp_path: Path,
    synthetic_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> Path:
    nodes, edges, tx = synthetic_frames
    nodes.to_parquet(tmp_path / "nodes.parquet", index=False)
    edges.to_parquet(tmp_path / "edges.parquet", index=False)
    tx.to_parquet(tmp_path / "transactions.parquet", index=False)
    return tmp_path
