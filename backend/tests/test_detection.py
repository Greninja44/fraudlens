"""Detection rule unit tests on hand-crafted data, plus an end-to-end evaluation on the synthetic dataset."""
import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, update

from app.detection import engine, rules
from app.models import Alert, Transaction
from app.services import ingestion
from app.services.settings_service import DEFAULT_DETECTION as P
from tests.conftest import DATASETS

T0 = datetime(2025, 3, 1, 10, 0)


def frame(rows):
    """rows: (sender, receiver, amount, minutes_offset[, ip])"""
    out = []
    for i, r in enumerate(rows):
        out.append({"id": i + 1, "txn_id": f"T{i}", "s": r[0], "r": r[1], "amount": float(r[2]),
                    "ts": pd.Timestamp(T0 + timedelta(minutes=r[3])), "ip": r[4] if len(r) > 4 else None, "ref": None})
    return pd.DataFrame(out)


def test_structuring_detected_and_single_near_threshold_ignored():
    day = 24 * 60
    df = frame([(1, 2, 9500, 0), (1, 2, 9400, day), (1, 3, 9800, 2 * day), (1, 2, 9100, 3 * day), (5, 6, 9900, 0)])
    f = rules.detect_structuring(df, P)
    s = [x for x in f if x.pattern == "structuring"]
    assert len(s) == 1 and s[0].account_id == 1 and set(s[0].txn_ids) == {1, 2, 3, 4}
    assert any("reporting threshold" in r for r in s[0].reasons)
    assert not any(x.account_id == 5 for x in f)


def test_structuring_outside_window_not_flagged():
    df = frame([(1, 2, 9500, 0), (1, 2, 9400, 10 * 24 * 60), (1, 2, 9800, 20 * 24 * 60)])
    assert not [x for x in rules.detect_structuring(df, P) if x.pattern == "structuring"]


def test_smurfing_many_senders_one_receiver():
    df = frame([(10, 99, 9000, 0), (11, 99, 9200, 60), (12, 99, 9300, 120), (13, 99, 9100, 180)])
    f = [x for x in rules.detect_structuring(df, P) if x.pattern == "smurfing"]
    assert len(f) == 1 and f[0].account_id == 99 and f[0].details["senders"] == 4


def test_rapid_pass_through():
    df = frame([(1, 2, 20000, 0), (2, 3, 19000, 180), (4, 5, 20000, 0), (5, 6, 19000, 60 * 24 * 5)])
    f = rules.detect_rapid_movement(df, P)
    assert [x.account_id for x in f] == [2]
    assert f[0].details["share"] == pytest.approx(0.95)


def test_circular_flow_requires_temporal_order():
    h = 60
    ok = frame([(1, 2, 50000, 0), (2, 3, 49000, 5 * h), (3, 4, 48000, 10 * h), (4, 1, 47000, 20 * h)])
    f = rules.detect_circular(ok, P)
    assert len(f) == 1 and f[0].details["path_account_ids"] == [1, 2, 3, 4, 1]
    # same edges but the closing hop happens *before* the money arrives -> not a flow
    bad = frame([(1, 2, 50000, 0), (2, 3, 49000, 10 * h), (3, 1, 48000, 5 * h)])  # no rotation is time-ordered
    assert rules.detect_circular(bad, P) == []
    # amounts that don't match are not a cycle of the same funds
    mismatch = frame([(1, 2, 50000, 0), (2, 3, 5000, 5 * h), (3, 1, 49000, 10 * h)])
    assert rules.detect_circular(mismatch, P) == []


def test_high_velocity():
    rows = [(7, 100 + i, 200, i * 3) for i in range(12)] + [(8, 50, 100, i * 600) for i in range(12)]
    f = rules.detect_velocity(frame(rows), P)
    assert len(f) == 1 and f[0].account_id == 7 and f[0].details["count"] == 12


def test_dormant_reactivation():
    day = 24 * 60
    df = frame([(1, 2, 100, 0), (3, 2, 50, 5 * day), (2, 9, 25000, 250 * day), (2, 9, 8000, 251 * day), (4, 5, 30000, 0), (4, 5, 30000, 3 * day)])
    f = rules.detect_dormant(df, P)
    assert len(f) == 1 and f[0].account_id == 2 and f[0].details["dormant_days"] >= 180


def test_unusual_amount_against_own_history():
    rows = [(1, 2, 100 + i, i * 1440) for i in range(20)] + [(1, 3, 20000, 21 * 1440)]
    f = rules.detect_unusual(frame(rows), P)
    assert any(x.details.get("zscore", 0) > P["unusual_zscore"] and 21 in x.txn_ids for x in f)


def test_shared_ip_network_ignores_same_holder():
    df = frame([(1, 9, 100, 0, "1.1.1.1"), (2, 9, 100, 1, "1.1.1.1"), (3, 9, 100, 2, "1.1.1.1"), (4, 9, 100, 3, "2.2.2.2"), (5, 9, 100, 4, "2.2.2.2"), (6, 9, 100, 5, "2.2.2.2")])
    holders = {1: "A", 2: "B", 3: "C", 4: "Same", 5: "Same", 6: "same"}
    f = rules.detect_networks(df, P, holders)
    ips = [x.details.get("ip_address") for x in f]
    assert "1.1.1.1" in ips and "2.2.2.2" not in ips


def test_severity_score_bounds():
    f = rules.Finding("structuring", "critical", 1.7, "t", [], 1, [1])
    assert 0 <= f.score <= 100


@pytest.fixture()
def loaded(db):
    with open(DATASETS / "synthetic_transactions.csv", "rb") as fh:
        b = ingestion.stage_upload(db, fh, "synthetic_transactions.csv", 100 * 2**20, None)
    db.commit()
    ingestion.run_import(db, b, b.column_mapping)
    return db


def test_end_to_end_detection_quality_on_synthetic_ground_truth(loaded):
    db = loaded
    run = engine.run_detection(db, engine.start_run(db, None))
    assert run.status == "completed"
    ev = run.evaluation
    assert ev["positives"] > 200
    # Thresholds are deliberately below the measured values to catch regressions (see docs/ARCHITECTURE.md).
    assert ev["flagged"]["recall"] >= 0.85
    assert ev["flagged"]["precision"] >= 0.7
    for pat, r in ev["per_pattern_recall"].items():
        assert r["recall"] >= 0.75, pat
    assert ev["model_roc_auc"] > 0.85
    # every rule alert has at least one human-readable reason and a valid severity/confidence
    for a in db.scalars(select(Alert).where(Alert.run_id == run.id)):
        assert a.reasons and a.severity in ("low", "medium", "high", "critical") and 0 <= a.confidence <= 1


def test_model_prediction_alone_never_flags(loaded):
    db = loaded
    run = engine.run_detection(db, engine.start_run(db, None))
    flagged_no_rule = db.scalars(select(Transaction).where(Transaction.is_flagged.is_(True))).all()
    assert all(t.patterns for t in flagged_no_rule), "a flagged transaction must have at least one rule-based indicator"
    model_only = db.scalars(select(Transaction).where(Transaction.anomaly_score >= 0.99)).all()
    for t in model_only:
        if not t.patterns:
            assert t.risk_score < P["flag_threshold"]


def test_ground_truth_labels_do_not_influence_detection(loaded):
    db = loaded
    r1 = engine.run_detection(db, engine.start_run(db, None))
    flags1 = {t.id: t.risk_score for t in db.scalars(select(Transaction))}
    # scramble labels
    rng = np.random.default_rng(0)
    for t in db.scalars(select(Transaction)):
        t.ground_truth_label = bool(rng.integers(0, 2))
        t.ground_truth_pattern = "scrambled"
    db.commit()
    r2 = engine.run_detection(db, engine.start_run(db, None))
    flags2 = {t.id: t.risk_score for t in db.scalars(select(Transaction))}
    assert flags1 == flags2
    assert r1.stats["flagged_transactions"] == r2.stats["flagged_transactions"]


def test_review_status_carries_over_between_runs(loaded):
    db = loaded
    r1 = engine.run_detection(db, engine.start_run(db, None))
    a = db.scalar(select(Alert).where(Alert.run_id == r1.id, Alert.pattern == "circular"))
    a.review_status = "dismissed"
    db.commit()
    r2 = engine.run_detection(db, engine.start_run(db, None))
    same = db.scalar(select(Alert).where(Alert.run_id == r2.id, Alert.details["fingerprint"].as_string() == a.details["fingerprint"]))
    assert same is not None and same.review_status == "dismissed"


def test_failed_transactions_excluded(db):
    csv = ("transaction_id,sender_account,receiver_account,amount,timestamp,status\n" +
           "\n".join(f"F{i},A1,B{i},9500,2025-01-0{1 + i}T10:00:00Z,failed" for i in range(4))).encode()
    b = ingestion.stage_upload(db, io.BytesIO(csv), "f.csv", 10**6, None)
    db.commit()
    ingestion.run_import(db, b, b.column_mapping)
    run = engine.run_detection(db, engine.start_run(db, None))
    assert run.stats["excluded_by_status"] == 4 and run.stats["rule_alerts"] == 0
