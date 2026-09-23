from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from moneygraph.config import load_analysis_config
from moneygraph.domain.roles import Role
from moneygraph.domain.schemas import NodeRoleRecord
from moneygraph.pipeline.role_engine import score_roles


def _row(gid: str, **changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "gid": gid,
        "depth": 2,
        "is_seed": False,
        "in_deg": 1,
        "out_deg": 1,
        "in_tx": 1,
        "out_tx": 1,
        "in_kzt": 10_000.0,
        "out_kzt": 10_000.0,
        "total_kzt": 20_000.0,
        "pagerank": 0.01,
        "authority": 0.01,
        "hub": 0.01,
        "betweenness": 0.0,
        "seed_reach_count": 0,
        "matched_out_ratio": 0.0,
        "fast_forward_0_2d_ratio": 0.0,
        "median_holding_days": 10.0,
        "active_in_days": 1,
        "active_out_days": 1,
        "max_daily_unique_payers": 1,
        "max_daily_unique_recipients": 1,
        "pass_through": 1.0,
        "pass_through_valid": True,
        "temporal_observation_reliable": True,
        "cross_cluster_bridge_signal": 0.0,
        "weak_component_size": 10,
        "truncated_by_depth": False,
    }
    return {**base, **changes}


@pytest.fixture
def role_features() -> pd.DataFrame:
    """Seven literal scenarios required by the case contract."""
    return pd.DataFrame(
        [
            _row(
                "collector",
                in_deg=18,
                in_tx=40,
                in_kzt=1_500_000.0,
                out_kzt=100_000.0,
                authority=0.95,
                seed_reach_count=9,
                matched_out_ratio=0.05,
                max_daily_unique_payers=12,
                pass_through=1 / 15,
            ),
            _row(
                "fanout",
                out_deg=22,
                out_tx=45,
                out_kzt=1_800_000.0,
                in_kzt=2_000_000.0,
                hub=0.98,
                max_daily_unique_recipients=15,
                pass_through=0.9,
            ),
            _row(
                "rapid",
                in_deg=3,
                out_deg=3,
                in_tx=8,
                out_tx=8,
                in_kzt=800_000.0,
                out_kzt=790_000.0,
                betweenness=0.45,
                matched_out_ratio=0.97,
                fast_forward_0_2d_ratio=0.94,
                median_holding_days=1.0,
                active_in_days=6,
                active_out_days=6,
                pass_through=0.9875,
            ),
            _row(
                "bridge",
                in_deg=3,
                out_deg=3,
                in_kzt=350_000.0,
                out_kzt=330_000.0,
                pagerank=0.99,
                betweenness=1.0,
                seed_reach_count=15,
                cross_cluster_bridge_signal=1.0,
                weak_component_size=200,
                pass_through=0.94,
            ),
            _row(
                "sink",
                depth=3,
                in_deg=4,
                out_deg=0,
                in_tx=7,
                out_tx=0,
                in_kzt=500_000.0,
                out_kzt=0.0,
                authority=0.55,
                pass_through=0.0,
            ),
            _row(
                "depth4",
                depth=4,
                in_deg=1,
                out_deg=0,
                in_kzt=60_000.0,
                out_kzt=0.0,
                pass_through=0.0,
                truncated_by_depth=True,
            ),
            _row(
                "isolate",
                depth=0,
                in_deg=0,
                out_deg=0,
                in_tx=0,
                out_tx=0,
                in_kzt=0.0,
                out_kzt=0.0,
                total_kzt=0.0,
                pass_through=float("nan"),
                pass_through_valid=False,
                temporal_observation_reliable=False,
                weak_component_size=1,
            ),
        ]
    )


def test_seven_synthetic_role_scenarios(role_features: pd.DataFrame) -> None:
    scored = score_roles(role_features).set_index("gid")

    assert scored.loc["collector", "role"] == Role.CONSOLIDATOR.value
    assert scored.loc["fanout", "role"] == Role.DISTRIBUTOR.value
    assert scored.loc["rapid", "role"] == Role.TRANSIT.value
    assert scored.loc["bridge", "role"] == Role.COORDINATOR.value
    assert scored.loc["sink", "role"] == Role.TERMINAL.value
    assert scored.loc["depth4", "role"] != Role.TERMINAL.value
    assert scored.loc["isolate", "role"] == Role.PERIPHERAL.value


def test_depth_four_terminal_score_is_capped(role_features: pd.DataFrame) -> None:
    scored = score_roles(role_features).set_index("gid")

    assert scored.loc["depth4", "terminal_score"] <= 0.25 + 1e-12
    assert scored.loc["sink", "terminal_score"] > 0.40


def test_seed_transit_signal_is_penalized() -> None:
    ordinary = _row(
        "ordinary",
        in_deg=3,
        out_deg=3,
        in_kzt=500_000.0,
        out_kzt=500_000.0,
        matched_out_ratio=0.98,
        fast_forward_0_2d_ratio=0.96,
        median_holding_days=0.0,
        betweenness=0.5,
        active_in_days=8,
        active_out_days=8,
    )
    seed = {**ordinary, "gid": "seed", "depth": 0, "is_seed": True, "pass_through_valid": False}

    scored = score_roles(pd.DataFrame([ordinary, seed])).set_index("gid")

    assert scored.loc["seed", "transit_score"] < scored.loc["ordinary", "transit_score"]
    assert scored.loc["seed", "transit_score"] <= scored.loc["ordinary", "transit_score"] * 0.65


def test_role_scores_and_confidence_stay_in_unit_interval(role_features: pd.DataFrame) -> None:
    scored = score_roles(role_features)
    score_columns = [f"{role.value}_score" for role in Role] + ["role_score"]

    assert scored[score_columns].notna().all().all()
    assert ((scored[score_columns] >= 0.0) & (scored[score_columns] <= 1.0)).all().all()
    assert set(scored["role"]) <= {role.value for role in Role}


def test_real_config_weights_and_nested_limits_are_applied(role_features: pd.DataFrame) -> None:
    config = load_analysis_config("config/default.yaml")

    configured = score_roles(role_features, config).set_index("gid")
    stricter = score_roles(
        role_features,
        {
            "roles": {"consolidator": {"incoming_amount": 1.0}},
            "limits": {"depth4_terminal_cap": 0.07, "seed_transit_cap": 0.10},
        },
    ).set_index("gid")

    assert configured.loc["depth4", "terminal_score"] <= 0.25
    assert stricter.loc["depth4", "terminal_score"] <= 0.07
    assert stricter.loc["collector", "consolidator_score"] == pytest.approx(5 / 6)
    assert (
        stricter.loc["collector", "consolidator_score"]
        != configured.loc["collector", "consolidator_score"]
    )


def test_unknown_role_weight_component_fails_loudly(role_features: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match=r"unknown.*typo_signal"):
        score_roles(
            role_features,
            {"roles": {"consolidator": {"typo_signal": 1.0}}},
        )


def test_role_engine_rejects_missing_or_duplicate_gid(role_features: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="contain gid"):
        score_roles(role_features.drop(columns="gid"))
    with pytest.raises(ValueError, match="unique gid"):
        score_roles(pd.concat([role_features, role_features.iloc[[0]]], ignore_index=True))


def test_node_role_schema_coerces_external_gid_and_is_immutable() -> None:
    record = NodeRoleRecord(
        gid=123,
        role=Role.PERIPHERAL,
        role_score=0.8,
        cluster_id=0,
        priority_score=0.2,
        evidence="Связей 0.",
    )

    assert record.gid == "123"
    with pytest.raises(ValidationError, match="frozen"):
        record.role_score = 0.9
    with pytest.raises(ValidationError, match="at most 200"):
        NodeRoleRecord(
            gid="123",
            role=Role.PERIPHERAL,
            role_score=0.8,
            cluster_id=0,
            priority_score=0.2,
            evidence="1" * 201,
        )
