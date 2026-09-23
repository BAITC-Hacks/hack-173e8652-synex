from __future__ import annotations

import pandas as pd
import pytest

from moneygraph.pipeline.priority import (
    PRIORITY_CONTRIBUTION_COLUMNS,
    score_priority,
)


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "gid": 30,
                "coordinator_score": 0.9,
                "consolidator_score": 0.8,
                "distributor_score": 0.2,
                "transit_score": 0.5,
                "pagerank_percentile": 0.95,
                "betweenness_percentile": 0.92,
                "seed_reach_count_percentile": 1.0,
                "total_kzt_percentile": 0.8,
            },
            {
                "gid": 10,
                "coordinator_score": 0.1,
                "consolidator_score": 0.2,
                "distributor_score": 0.3,
                "transit_score": 0.2,
                "pagerank_percentile": 0.1,
                "betweenness_percentile": 0.1,
                "seed_reach_count_percentile": 0.0,
                "total_kzt_percentile": 0.2,
            },
            {
                "gid": 20,
                "coordinator_score": 0.4,
                "consolidator_score": 0.5,
                "distributor_score": 0.4,
                "transit_score": 0.5,
                "pagerank_percentile": 0.5,
                "betweenness_percentile": 0.5,
                "seed_reach_count_percentile": 0.5,
                "total_kzt_percentile": 0.5,
            },
        ]
    )


def test_priority_is_explainable_bounded_and_has_driver_contributions() -> None:
    scored = score_priority(_features())

    assert set(PRIORITY_CONTRIBUTION_COLUMNS) <= set(scored.columns)
    assert scored["priority_score"].between(0.0, 1.0).all()
    assert scored["priority_raw"].between(0.0, 1.0).all()
    assert scored[PRIORITY_CONTRIBUTION_COLUMNS].ge(0.0).all().all()
    assert scored[PRIORITY_CONTRIBUTION_COLUMNS].sum(axis=1).equals(scored["priority_raw"])
    assert scored.set_index("gid").loc[30, "priority_score"] == pytest.approx(1.0)
    assert scored.set_index("gid").loc[10, "priority_score"] == pytest.approx(0.0)


def test_priority_is_deterministic_for_shuffled_input() -> None:
    original = score_priority(_features()).sort_values("gid").reset_index(drop=True)
    shuffled = (
        score_priority(_features().sample(frac=1.0, random_state=7))
        .sort_values("gid")
        .reset_index(drop=True)
    )

    pd.testing.assert_frame_equal(original, shuffled)


def test_partial_or_unknown_priority_config_never_silently_falls_back() -> None:
    with pytest.raises(ValueError, match="priority weight keys"):
        score_priority(_features(), {"priority": {"coordinator": 1.0}})

    with pytest.raises(ValueError, match="unknown_driver"):
        score_priority(_features(), {"priority": {"unknown_driver": 1.0}})


def test_priority_rejects_invalid_boundary_inputs() -> None:
    with pytest.raises(ValueError, match="contain gid"):
        score_priority(_features().drop(columns="gid"))
    with pytest.raises(ValueError, match="unique gid"):
        score_priority(pd.concat([_features(), _features().iloc[[0]]], ignore_index=True))
    invalid_weights = {
        name: 0.2
        for name in (
            "coordinator",
            "consolidator",
            "distributor",
            "transit",
            "pagerank",
            "betweenness",
            "seed_reach",
            "turnover",
        )
    }
    with pytest.raises(ValueError, match="sum to 1"):
        score_priority(_features(), {"priority": invalid_weights})
