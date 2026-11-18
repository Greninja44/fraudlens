"""Detection run orchestration: load transactions, run rules + anomaly model, fuse risk scores, persist alerts,
and (when synthetic ground-truth labels exist) evaluate detection quality.

Risk fusion (0-100) per transaction:
    rule_risk  = 1 - prod(1 - alert_score/100)            over all rule alerts involving the transaction
    model_risk = ml_weight * clip((percentile - 0.9) / 0.1, 0, 1)   (only the top 10% most anomalous contribute)
    risk       = 100 * (1 - (1 - rule_risk) * (1 - model_risk))
With the default ml_weight=0.35 the anomaly model alone can raise risk to at most 35, below the default
flag threshold of 50: a model prediction by itself never flags a transaction.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.detection import ml, rules
from app.detection.rules import Finding
from app.models import Account, Alert, DetectionRun, Entity, Transaction, utcnow
from app.services.settings_service import get_setting

log = logging.getLogger(__name__)
EXCLUDED_STATUSES = ("failed", "declined", "reversed", "cancelled", "rejected")


def risk_level(score: float) -> str:
    return "critical" if score >= 80 else "high" if score >= 60 else "medium" if score >= 30 else "low"


def load_frame(db: Session) -> tuple[pd.DataFrame, dict[int, str]]:
    q = select(Transaction.id, Transaction.txn_id, Transaction.sender_account_id.label("s"),
               Transaction.receiver_account_id.label("r"), Transaction.amount, Transaction.timestamp.label("ts"),
               Transaction.ip_address.label("ip"), Transaction.reference.label("ref"), Transaction.status,
               Transaction.ground_truth_label.label("gt_label"), Transaction.ground_truth_pattern.label("gt_pattern")
               ).order_by(Transaction.id)  # deterministic input order => reproducible runs (model subsampling depends on it)
    df = pd.DataFrame(db.execute(q).all(), columns=["id", "txn_id", "s", "r", "amount", "ts", "ip", "ref", "status", "gt_label", "gt_pattern"])
    if not df.empty:
        df["amount"] = df.amount.astype(float)
        df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_localize(None)  # naive UTC for numpy ops
        df["ip"] = df.ip.astype(object).where(df.ip.notna(), None)
    holder = dict(db.execute(select(Account.id, Account.holder_name)).all())
    return df, {k: (v or "") for k, v in holder.items()}


def _fingerprint(f: Finding) -> str:
    return hashlib.sha1(f"{f.pattern}|{f.account_id}|{sorted(f.txn_ids)}".encode()).hexdigest()


def run_detection(db: Session, run: DetectionRun) -> DetectionRun:
    t_start = time.time()
    params = get_setting(db, "detection")
    run.params = params
    df_all, holder = load_frame(db)
    if df_all.empty:
        run.status, run.completed_at = "completed", utcnow()
        run.stats = {"transactions": 0, "message": "No transactions to analyse"}
        db.commit()
        return run
    df = df_all[~df_all.status.fillna("").str.lower().isin(EXCLUDED_STATUSES)].reset_index(drop=True)
    timings: dict[str, float] = {}
    if len(df) < 2:
        db.execute(update(Transaction).values(risk_score=0.0, risk_level="low", is_flagged=False, patterns=[], anomaly_score=None))
        db.execute(update(Account).values(risk_score=0.0, risk_level="low", alert_count=0, patterns=[]))
        run.status, run.completed_at = "completed", utcnow()
        run.stats = {"transactions": int(len(df_all)), "analysed": int(len(df)), "excluded_by_status": int(len(df_all) - len(df)),
                     "alerts": 0, "rule_alerts": 0, "model_alerts": 0, "alerts_by_pattern": {}, "flagged_transactions": 0,
                     "high_risk_accounts": 0, "flagged_value": 0.0, "message": "Too few analysable transactions"}
        db.commit()
        return run

    findings: list[Finding] = []
    for name, fn in [("structuring", lambda: rules.detect_structuring(df, params)),
                     ("rapid_movement", lambda: rules.detect_rapid_movement(df, params)),
                     ("circular", lambda: rules.detect_circular(df, params)),
                     ("unusual_behavior", lambda: rules.detect_unusual(df, params)),
                     ("high_velocity", lambda: rules.detect_velocity(df, params)),
                     ("account_network", lambda: rules.detect_networks(df, params, holder)),
                     ("dormant_reactivation", lambda: rules.detect_dormant(df, params))]:
        t0 = time.time()
        findings.extend(fn())
        timings[name] = round(time.time() - t0, 3)

    # ---- anomaly model
    t0 = time.time()
    model_out = ml.run_isolation_forest(df, float(params["reporting_threshold"]), float(params["ml_contamination"]))
    timings["isolation_forest"] = round(time.time() - t0, 3)
    for ix in np.where(model_out.is_outlier.to_numpy())[0]:
        row, m = df.iloc[ix], model_out.iloc[ix]
        reasons = [f"Isolation Forest ranks this transaction in the top {max(1 - m.anomaly_percentile, 0.0001):.1%} most unusual"]
        reasons += [f"Unusual {t['label']} (deviation {t['deviation']:.1f})" for t in m.top_features]
        reasons.append("Model prediction of statistical unusualness - not evidence of wrongdoing")
        findings.append(Finding("ml_anomaly", "low", float(m.anomaly_percentile), "Statistically unusual transaction (model)",
                                reasons, int(row.s), [int(row.id)], [int(row.s), int(row.r)],
                                {"anomaly_percentile": round(float(m.anomaly_percentile), 4), "top_features": m.top_features},
                                kind="model"))

    # ---- risk fusion per transaction
    rule_keep = defaultdict(lambda: 1.0)
    txn_patterns: dict[int, set] = defaultdict(set)
    for f in findings:
        if f.kind != "rule":
            continue
        for tid in f.txn_ids:
            rule_keep[tid] *= 1 - f.score / 100
            txn_patterns[tid].add(f.pattern)
    ml_w = float(params["ml_weight"])
    pct = model_out.anomaly_percentile.to_numpy()
    model_risk = ml_w * np.clip((pct - 0.9) / 0.1, 0, 1)
    ids = df.id.to_numpy()
    risk = np.array([100 * (1 - rule_keep[int(t)] * (1 - mr)) for t, mr in zip(ids, model_risk)])
    flag_thr = float(params["flag_threshold"])
    flagged = risk >= flag_thr

    # ---- persist (previous run's review decisions are carried over for identical alerts)
    prev = db.scalar(select(DetectionRun.id).where(DetectionRun.status == "completed", DetectionRun.id != run.id).order_by(DetectionRun.id.desc()).limit(1))
    carried = {}
    if prev:
        for a in db.scalars(select(Alert).where(Alert.run_id == prev, Alert.review_status != "new")):
            carried[a.details.get("fingerprint")] = (a.review_status, a.reviewed_by_id, a.reviewed_at)
    ts_by_id = dict(zip(df.id.astype(int), df.ts))
    amt_by_id = dict(zip(df.id.astype(int), df.amount))
    for f in findings:
        fp = _fingerprint(f)
        tss = [ts_by_id[t] for t in f.txn_ids if t in ts_by_id]
        st = carried.get(fp, ("new", None, None))
        db.add(Alert(run_id=run.id, kind=f.kind, pattern=f.pattern, severity=f.severity, confidence=round(f.confidence, 3),
                     score=f.score, title=f.title[:255], reasons=f.reasons,
                     details={**f.details, "account_ids": f.account_ids, "fingerprint": fp},
                     account_id=f.account_id, transaction_ids=[int(x) for x in f.txn_ids],
                     first_txn_at=min(tss).tz_localize('UTC').to_pydatetime() if tss else None,
                     last_txn_at=max(tss).tz_localize('UTC').to_pydatetime() if tss else None,
                     total_amount=Decimal(str(round(sum(amt_by_id.get(t, 0) for t in f.txn_ids), 2))),
                     review_status=st[0], reviewed_by_id=st[1], reviewed_at=st[2]))
    db.flush()
    # reset then write transaction scores
    db.execute(update(Transaction).values(risk_score=0.0, risk_level="low", is_flagged=False, patterns=[], anomaly_score=None))
    updates = [{"id": int(t), "risk_score": round(float(rk), 1), "risk_level": risk_level(rk), "is_flagged": bool(fl),
                "patterns": sorted(txn_patterns.get(int(t), ())), "anomaly_score": round(float(p), 4)}
               for t, rk, fl, p in zip(ids, risk, flagged, pct)]
    for i in range(0, len(updates), 5000):
        db.execute(update(Transaction), updates[i:i + 5000])

    # ---- account & entity risk
    acct_keep = defaultdict(lambda: 1.0)
    acct_alerts = defaultdict(int)
    acct_patterns: dict[int, set] = defaultdict(set)
    for f in findings:
        if f.kind != "rule":
            continue
        for a in set(f.account_ids) | {f.account_id}:
            if a is None:
                continue
            w = 1.0 if a == f.account_id else 0.6
            acct_keep[a] *= 1 - w * f.score / 100
            acct_alerts[a] += 1
            acct_patterns[a].add(f.pattern)
    db.execute(update(Account).values(risk_score=0.0, risk_level="low", alert_count=0, patterns=[]))
    acc_updates = [{"id": a, "risk_score": round(100 * (1 - k), 1), "risk_level": risk_level(100 * (1 - k)),
                    "alert_count": acct_alerts[a], "patterns": sorted(acct_patterns[a])} for a, k in acct_keep.items()]
    if acc_updates:
        db.execute(update(Account), acc_updates)
    db.flush()
    ent_risk = defaultdict(float)
    for eid, rs in db.execute(select(Account.entity_id, Account.risk_score)):
        if eid is not None:
            ent_risk[eid] = max(ent_risk[eid], rs or 0.0)
    db.execute(update(Entity).values(risk_score=0.0))
    if ent_risk:
        db.execute(update(Entity), [{"id": k, "risk_score": v} for k, v in ent_risk.items() if v > 0])

    by_pattern = defaultdict(int)
    for f in findings:
        by_pattern[f.pattern] += 1
    run.stats = {
        "transactions": int(len(df_all)), "analysed": int(len(df)), "excluded_by_status": int(len(df_all) - len(df)),
        "alerts": len(findings), "rule_alerts": sum(1 for f in findings if f.kind == "rule"),
        "model_alerts": sum(1 for f in findings if f.kind == "model"),
        "alerts_by_pattern": dict(by_pattern), "flagged_transactions": int(flagged.sum()),
        "high_risk_accounts": sum(1 for u in acc_updates if u["risk_score"] >= 60),
        "flagged_value": round(float(df.amount.to_numpy()[flagged].sum()), 2),
        "timings_sec": timings, "duration_sec": round(time.time() - t_start, 2),
    }
    run.evaluation = evaluate(df, flagged, risk, pct, txn_patterns, model_out.is_outlier.to_numpy())
    run.status, run.completed_at = "completed", utcnow()
    db.commit()
    return run


def evaluate(df, flagged, risk, pct, txn_patterns, outlier) -> dict | None:
    """Compare flags against synthetic ground-truth labels, when the dataset carries them."""
    labelled = df.gt_label.notna().to_numpy()
    if labelled.sum() == 0:
        return None
    y = df.gt_label.fillna(False).astype(bool).to_numpy()[labelled]

    def prf(pred):
        pred = pred[labelled]
        tp = int((pred & y).sum()); fp = int((pred & ~y).sum()); fn = int((~pred & y).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}

    any_rule = np.array([bool(txn_patterns.get(int(t))) for t in df.id])
    result = {
        "labelled_transactions": int(labelled.sum()), "positives": int(y.sum()),
        "flagged": prf(flagged), "any_rule_hit": prf(any_rule), "model_outlier_only": prf(outlier),
        "per_pattern_recall": {},
        "note": "Computed against synthetic ground-truth labels. Labels are never used by the detectors.",
    }
    try:
        from sklearn.metrics import average_precision_score, roc_auc_score
        result["model_roc_auc"] = round(float(roc_auc_score(y, pct[labelled])), 3)
        result["model_average_precision"] = round(float(average_precision_score(y, pct[labelled])), 3)
        result["risk_roc_auc"] = round(float(roc_auc_score(y, risk[labelled])), 3)
    except ValueError:
        pass
    gtp = df.gt_pattern.fillna("").to_numpy()
    for pat in sorted({p for p in gtp[df.gt_label.fillna(False).astype(bool).to_numpy()] if p}):
        m = gtp == pat
        result["per_pattern_recall"][pat] = {"labelled": int(m.sum()), "flagged": int((flagged & m).sum()),
                                             "recall": round(float((flagged & m).sum() / m.sum()), 3)}
    return result


def start_run(db: Session, user_id: int | None) -> DetectionRun:
    run = DetectionRun(status="running", triggered_by_id=user_id, params={})
    db.add(run)
    db.commit()
    return run


def execute_run(session_factory, run_id: int) -> None:
    db = session_factory()
    try:
        run = db.get(DetectionRun, run_id)
        try:
            run_detection(db, run)
        except Exception as e:  # pragma: no cover - logged and surfaced on the run
            log.exception("detection run %s failed", run_id)
            db.rollback()
            run = db.get(DetectionRun, run_id)
            db.execute(delete(Alert).where(Alert.run_id == run_id))
            run.status, run.error, run.completed_at = "failed", f"{type(e).__name__}: {str(e)[:500]}", utcnow()
            db.commit()
    finally:
        db.close()
