from __future__ import annotations

import pandas as pd
import pytest

from moneygraph.pipeline.ingestion import DatasetBundle, load_dataset
from moneygraph.pipeline.validation import DataValidationError, validate_dataset


def test_load_and_validate_accepts_consistent_parquet(synthetic_data_dir) -> None:
    bundle = load_dataset(synthetic_data_dir)

    report = validate_dataset(bundle)

    assert report.is_valid is True
    assert report.counts == {"nodes": 6, "edges": 4, "transactions": 5, "seed": 2}
    assert report.isolated_gids == ("isolated",)


def test_load_dataset_canonicalizes_large_integer_ids_without_mutating_source(tmp_path) -> None:
    large_gid = 100_000_000_343_175_100
    nodes = pd.DataFrame(
        [{"gid": large_gid, "depth": 0, "is_seed": True}],
    )
    edges = pd.DataFrame(
        columns=pd.Index(["src", "dst", "sum_kzt", "n_tx", "depth"]),
    ).astype(
        {"src": "int64", "dst": "int64", "sum_kzt": "float64", "n_tx": "int64", "depth": "int8"}
    )
    tx = pd.DataFrame(
        columns=pd.Index(["src", "dst", "date", "sum_kzt"]),
    ).astype({"src": "int64", "dst": "int64", "sum_kzt": "float64"})

    nodes.to_parquet(tmp_path / "nodes.parquet", index=False)
    edges.to_parquet(tmp_path / "edges.parquet", index=False)
    tx.to_parquet(tmp_path / "transactions.parquet", index=False)

    loaded = load_dataset(tmp_path)

    assert loaded.nodes.loc[0, "gid"] == str(large_gid)
    assert str(loaded.nodes["gid"].dtype) == "string"
    assert str(loaded.edges["src"].dtype) == "string"
    assert str(loaded.transactions["dst"].dtype) == "string"
    assert nodes.loc[0, "gid"] == large_gid


def test_validation_rejects_edge_transaction_amount_mismatch(synthetic_frames) -> None:
    nodes, edges, tx = synthetic_frames
    changed_edges = edges.assign(
        sum_kzt=edges["sum_kzt"].where(edges.index != 0, edges["sum_kzt"] + 1.0)
    )

    with pytest.raises(DataValidationError, match="aggregation"):
        validate_dataset(DatasetBundle(nodes=nodes, edges=changed_edges, transactions=tx))


def test_validation_rejects_non_positive_transaction(synthetic_frames) -> None:
    nodes, edges, tx = synthetic_frames
    invalid_tx = pd.concat(
        [
            tx,
            pd.DataFrame(
                [{"src": "seed", "dst": "sink", "date": pd.Timestamp("2026-07-01"), "sum_kzt": 0.0}]
            ),
        ],
        ignore_index=True,
    )

    with pytest.raises(DataValidationError, match="positive"):
        validate_dataset(DatasetBundle(nodes=nodes, edges=edges, transactions=invalid_tx))


def test_validation_records_self_loop_instead_of_hiding_it(synthetic_frames) -> None:
    nodes, _, tx = synthetic_frames
    loop = pd.DataFrame(
        [{"src": "bridge", "dst": "bridge", "date": pd.Timestamp("2026-07-07"), "sum_kzt": 6_000.0}]
    )
    tx_with_loop = pd.concat([tx, loop], ignore_index=True)
    edges_with_loop = (
        tx_with_loop.groupby(["src", "dst"], sort=True)
        .agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        .reset_index()
    )
    edges_with_loop["depth"] = edges_with_loop["dst"].map(nodes.set_index("gid")["depth"])

    report = validate_dataset(
        DatasetBundle(nodes=nodes, edges=edges_with_loop, transactions=tx_with_loop)
    )

    assert report.self_loops == 1
    assert any("self-loop" in warning.lower() for warning in report.warnings)
