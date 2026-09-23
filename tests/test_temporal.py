from __future__ import annotations

import pandas as pd
import pytest

from moneygraph.pipeline.temporal import compute_temporal_features, fifo_match_node


def test_fifo_matching_never_uses_future_inflow() -> None:
    transactions = pd.DataFrame(
        [
            {"src": "node", "dst": "x", "date": pd.Timestamp("2026-07-01"), "sum_kzt": 40.0},
            {"src": "a", "dst": "node", "date": pd.Timestamp("2026-07-02"), "sum_kzt": 100.0},
            {"src": "node", "dst": "y", "date": pd.Timestamp("2026-07-03"), "sum_kzt": 60.0},
        ]
    )

    metrics = fifo_match_node("node", transactions)

    assert metrics.matched_volume == pytest.approx(60.0)
    assert metrics.unmatched_out_volume == pytest.approx(40.0)
    assert metrics.matched_out_ratio == pytest.approx(0.60)
    assert metrics.fast_forward_0_2d_ratio == pytest.approx(1.0)
    assert metrics.median_holding_days == pytest.approx(1.0)


def test_fifo_matching_is_volume_weighted() -> None:
    transactions = pd.DataFrame(
        [
            {"src": "a", "dst": "node", "date": pd.Timestamp("2026-07-01"), "sum_kzt": 100.0},
            {"src": "node", "dst": "x", "date": pd.Timestamp("2026-07-02"), "sum_kzt": 60.0},
            {"src": "node", "dst": "y", "date": pd.Timestamp("2026-07-04"), "sum_kzt": 20.0},
        ]
    )

    metrics = fifo_match_node("node", transactions)

    assert metrics.matched_volume == pytest.approx(80.0)
    assert metrics.fast_forward_0_2d_ratio == pytest.approx(0.75)
    assert metrics.median_holding_days == pytest.approx(1.0)


def test_temporal_features_include_daily_activity_and_seed_reliability(synthetic_frames) -> None:
    nodes, _, transactions = synthetic_frames

    features = compute_temporal_features(nodes, transactions).set_index("gid")

    seed = features.loc["seed"]
    bridge = features.loc["bridge"]
    assert seed["active_out_days"] == 2
    assert seed["max_daily_unique_recipients"] == 1
    assert bool(seed["temporal_observation_reliable"]) is False
    assert bridge["active_in_days"] == 1
    assert bridge["active_out_days"] == 2
    assert bridge["max_daily_unique_payers"] == 1
    assert bridge["matched_out_ratio"] == pytest.approx(23_000 / 23_000)
    assert bridge["fast_forward_0_2d_ratio"] == pytest.approx(23_000 / 23_000)
