from __future__ import annotations

import re

import pandas as pd

from moneygraph.pipeline.explanations import build_evidence, build_priority_explanation


def test_evidence_is_numeric_russian_non_empty_and_at_most_200_characters() -> None:
    rows = pd.DataFrame(
        [
            {
                "gid": 1,
                "role": "consolidator",
                "in_deg": 14,
                "out_deg": 2,
                "in_kzt": 8_400_000.0,
                "out_kzt": 924_000.0,
                "matched_out_ratio": 0.11,
                "depth": 2,
                "is_seed": False,
                "truncated_by_depth": False,
            },
            {
                "gid": 2,
                "role": "transit",
                "in_deg": 3,
                "out_deg": 3,
                "in_kzt": 1_000_000.0,
                "out_kzt": 910_000.0,
                "matched_out_ratio": 0.91,
                "fast_forward_0_2d_ratio": 0.76,
                "median_holding_days": 1.0,
                "depth": 2,
                "is_seed": False,
                "truncated_by_depth": False,
            },
            {
                "gid": 3,
                "role": "peripheral",
                "in_deg": 1,
                "out_deg": 0,
                "in_kzt": 12_000.0,
                "out_kzt": 0.0,
                "depth": 4,
                "is_seed": False,
                "truncated_by_depth": True,
            },
        ]
    )

    evidence = rows.apply(build_evidence, axis=1)

    assert evidence.map(bool).all()
    assert evidence.map(len).le(200).all()
    assert evidence.map(lambda value: bool(re.search(r"\d", value))).all()
    assert evidence.map(lambda value: bool(re.search(r"[А-Яа-яЁё]", value))).all()
    assert "depth=4" in evidence.iloc[2]
    assert "не подтвержд" in evidence.iloc[2]


def test_priority_explanation_lists_numeric_drivers_without_accusation() -> None:
    why = build_priority_explanation(
        {
            "priority_score": 0.93,
            "priority_coordinator": 0.21,
            "priority_seed_reach": 0.10,
            "priority_consolidator": 0.16,
        }
    )

    assert "0.93" in why
    assert "0.21" in why
    assert "провер" in why.lower()
    assert "преступ" not in why.lower()


def test_evidence_covers_every_remaining_role_and_seed_limitation() -> None:
    cases = [
        {
            "role": "distributor",
            "in_deg": 1,
            "out_deg": 8,
            "in_kzt": 500_000,
            "out_kzt": 450_000,
            "out_tx": 12,
        },
        {
            "role": "terminal",
            "in_deg": 4,
            "out_deg": 0,
            "in_kzt": 900_000,
            "out_kzt": 0,
        },
        {
            "role": "coordinator",
            "in_deg": 3,
            "out_deg": 3,
            "betweenness_percentile": 0.87,
            "seed_reach_count": 6,
            "cross_cluster_bridge_signal": 0.75,
        },
        {
            "role": "peripheral",
            "in_deg": 1,
            "out_deg": 1,
            "in_kzt": 5_000,
            "out_kzt": 5_000,
            "is_seed": True,
        },
        {
            "role": "peripheral",
            "in_deg": 0,
            "out_deg": 0,
            "in_kzt": 0,
            "out_kzt": 0,
        },
    ]

    evidence = [build_evidence(case) for case in cases]

    assert all(re.search(r"\d", value) for value in evidence)
    assert all(len(value) <= 200 for value in evidence)
    assert "seed=1" in evidence[3]
    assert "наблюдаемых связей 0" in evidence[4].lower()
