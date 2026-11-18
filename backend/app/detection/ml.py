"""Unsupervised anomaly model (Isolation Forest) over per-transaction behavioural features.

Output is a *model prediction* of statistical unusualness, not evidence of fraud. Per-transaction
explanations list the features whose values are most extreme relative to the population (robust z-scores);
this is an approximation to help reviewers, not an exact attribution of the model's decision.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

FEATURE_LABELS = {
    "log_amount": "transaction amount",
    "sender_amount_z": "amount relative to sender's history",
    "receiver_amount_z": "amount relative to receiver's history",
    "night": "time of day (00:00-05:00)",
    "sender_24h_count": "sender's transactions in previous 24h",
    "log_secs_since_prev": "time since sender's previous transaction (short)",
    "receiver_in_degree": "number of distinct senders to receiver",
    "sender_out_degree": "number of distinct recipients of sender",
    "near_threshold": "amount just below reporting threshold",
    "round_amount": "round amount",
    "inflow_48h_ratio": "funds received by sender in previous 48h vs amount",
}


def build_features(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    d = df.sort_values("ts").copy()
    f = pd.DataFrame(index=d.index)
    la = np.log1p(d.amount)
    f["log_amount"] = la

    def robust_z(group_col):
        med = la.groupby(d[group_col]).transform("median")
        mad = (la - med).abs().groupby(d[group_col]).transform("median") * 1.4826
        return ((la - med) / mad.clip(lower=0.25)).fillna(0)

    f["sender_amount_z"] = robust_z("s")
    f["receiver_amount_z"] = robust_z("r")
    f["night"] = d.ts.dt.hour.between(0, 4).astype(float)
    # rolling 24h count per sender
    cnt = np.zeros(len(d))
    secs = np.full(len(d), 30 * 86400.0)
    pos = {ix: k for k, ix in enumerate(d.index)}
    for _, g in d.groupby("s"):
        t = g.ts.to_numpy()
        st = np.searchsorted(t, t - np.timedelta64(1, "D"), side="left")
        c = np.arange(len(t)) - st + 1
        gaps = np.diff(t).astype("timedelta64[s]").astype(float)
        sec = np.concatenate([[30 * 86400.0], gaps])
        for k, ix in enumerate(g.index):
            cnt[pos[ix]], secs[pos[ix]] = c[k], sec[k]
    f["sender_24h_count"] = cnt
    f["log_secs_since_prev"] = -np.log1p(np.clip(secs, 0, None))  # higher = shorter gap
    f["receiver_in_degree"] = np.log1p(d.groupby("r").s.transform("nunique"))
    f["sender_out_degree"] = np.log1p(d.groupby("s").r.transform("nunique"))
    f["near_threshold"] = ((d.amount >= 0.85 * threshold) & (d.amount < threshold)).astype(float)
    f["round_amount"] = ((d.amount % 100 == 0) & (d.amount >= 1000)).astype(float)
    # inbound to sender in the previous 48h relative to this amount (pass-through signal)
    inflow = np.zeros(len(d))
    recv = {a: g for a, g in d.groupby("r")}
    for a, g in d.groupby("s"):
        rg = recv.get(a)
        if rg is None:
            continue
        rt = rg.ts.to_numpy()
        ra = np.concatenate([[0.0], np.cumsum(rg.amount.to_numpy())])
        t = g.ts.to_numpy()
        hi = np.searchsorted(rt, t, side="left")
        lo = np.searchsorted(rt, t - np.timedelta64(48, "h"), side="left")
        vals = ra[hi] - ra[lo]
        for k, ix in enumerate(g.index):
            inflow[pos[ix]] = vals[k]
    f["inflow_48h_ratio"] = np.log1p(inflow / d.amount.to_numpy())
    return f.loc[df.index]


def run_isolation_forest(df: pd.DataFrame, threshold: float, contamination: float, seed: int = 0) -> pd.DataFrame:
    """Returns DataFrame indexed like df with: anomaly_percentile (0-1), is_outlier, top_features (list)."""
    feats = build_features(df, threshold)
    X = feats.to_numpy(dtype=float)
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed, n_jobs=-1)
    model.fit(X)
    raw = -model.score_samples(X)  # higher = more anomalous
    pct = pd.Series(raw, index=df.index).rank(pct=True)
    pred = model.predict(X) == -1
    med = feats.median()
    mad = (feats - med).abs().median().replace(0, np.nan)
    std = feats.std().replace(0, 1)
    scale = (mad * 1.4826).fillna(std)
    zs = ((feats - med) / scale).abs()
    tops = []
    for ix in df.index:
        row = zs.loc[ix].sort_values(ascending=False)
        tops.append([{"feature": k, "label": FEATURE_LABELS[k], "value": round(float(feats.at[ix, k]), 3),
                      "deviation": round(float(v), 2)} for k, v in row.head(3).items() if v > 1.5])
    return pd.DataFrame({"anomaly_raw": raw, "anomaly_percentile": pct, "is_outlier": pred, "top_features": tops}, index=df.index)
