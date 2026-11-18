"""Explainable rule-based detectors.

Every detector takes a prepared transaction DataFrame and parameter dict, and returns a list of `Finding`s.
A Finding is an *indicator* of possibly suspicious activity with human-readable reasons - never a verdict.

DataFrame columns expected: id, txn_id, s (sender account id), r (receiver account id), amount (float),
ts (datetime64[ns, UTC]), ip, ref, ttype, acct_name (dict lookup provided separately).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SEVERITY_BASE = {"low": 25.0, "medium": 50.0, "high": 75.0, "critical": 92.0}

PATTERN_LABELS = {
    "structuring": "Structuring",
    "smurfing": "Smurfing (structured deposits from multiple senders)",
    "rapid_movement": "Rapid fund movement (pass-through)",
    "circular": "Circular transaction flow",
    "unusual_behavior": "Unusual transaction behaviour",
    "high_velocity": "High-velocity transactions",
    "account_network": "Suspicious account network",
    "dormant_reactivation": "Dormant account reactivation",
    "ml_anomaly": "Statistical anomaly (model prediction)",
}


@dataclass
class Finding:
    pattern: str
    severity: str
    confidence: float
    title: str
    reasons: list[str]
    account_id: int | None
    txn_ids: list[int]
    account_ids: list[int] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    kind: str = "rule"

    @property
    def score(self) -> float:
        return round(SEVERITY_BASE[self.severity] * min(max(self.confidence, 0.0), 1.0), 1)


def money(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_ts(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------ 1. structuring & smurfing
def detect_structuring(df: pd.DataFrame, p: dict) -> list[Finding]:
    T = float(p["reporting_threshold"])
    lo = T * (1 - float(p["structuring_band"]))
    window = pd.Timedelta(days=float(p["structuring_window_days"]))
    min_n = int(p["structuring_min_count"])
    near = df[(df.amount >= lo) & (df.amount < T)].sort_values("ts")
    out: list[Finding] = []

    def clusters(g: pd.DataFrame):
        ts = g.ts.to_numpy()
        start = 0
        for i in range(1, len(g) + 1):
            if i == len(g) or ts[i] - ts[start] > window.to_timedelta64():
                if i - start >= min_n:
                    yield g.iloc[start:i]
                start = i

    # Same sender splitting funds into several near-threshold transfers
    for sender, g in near.groupby("s"):
        for c in clusters(g):
            n, total = len(c), float(c.amount.sum())
            if total <= T:
                continue
            closeness = float(((c.amount - lo) / (T - lo)).mean())  # 1.0 = right under the threshold
            conf = min(0.95, 0.55 + 0.07 * (n - min_n) + 0.25 * closeness)
            sev = "high" if n >= 5 or total >= 3 * T else "medium"
            days = (c.ts.max() - c.ts.min()).total_seconds() / 86400
            out.append(Finding(
                "structuring", sev, conf, f"{n} near-threshold transfers totalling {money(total)}",
                [f"{n} transactions between {money(lo)} and {money(T)} (just below the {money(T)} reporting threshold) within {days:.1f} days",
                 f"Combined value {money(total)} exceeds the threshold {total / T:.1f}x",
                 f"Receiving accounts: {c.r.nunique()}"],
                int(sender), c.id.tolist(), sorted({int(sender), *map(int, c.r.unique())}),
                {"threshold": T, "count": n, "total": total, "span_days": round(days, 2)}))

    # Many distinct senders depositing near-threshold amounts into one account
    min_senders = int(p["smurfing_min_senders"])
    for recv, g in near.groupby("r"):
        ts = g.ts.to_numpy()
        start, i = 0, 0
        while i <= len(g):
            if i == len(g) or ts[i] - ts[start] > window.to_timedelta64():
                c = g.iloc[start:i]
                if c.s.nunique() >= min_senders:
                    total = float(c.amount.sum())
                    k = c.s.nunique()
                    conf = min(0.95, 0.6 + 0.07 * (k - min_senders) + 0.05 * (len(c) - k))
                    days = (c.ts.max() - c.ts.min()).total_seconds() / 86400
                    out.append(Finding(
                        "smurfing", "high" if k >= 4 else "medium", conf,
                        f"{k} senders made near-threshold deposits totalling {money(total)}",
                        [f"{k} distinct accounts sent {len(c)} transfers just below {money(T)} to the same account within {days:.1f} days",
                         f"Combined value {money(total)}",
                         "Pattern consistent with smurfing: splitting funds across several people to avoid reporting"],
                        int(recv), c.id.tolist(), sorted({int(recv), *map(int, c.s.unique())}),
                        {"threshold": T, "senders": k, "count": len(c), "total": total}))
                start = i if i < len(g) else start
            i += 1
    return out


# ------------------------------------------------------------------ 2. rapid movement
def detect_rapid_movement(df: pd.DataFrame, p: dict) -> list[Finding]:
    window = pd.Timedelta(hours=float(p["rapid_window_hours"]))
    ratio = float(p["rapid_min_ratio"])
    min_amt = float(p["rapid_min_amount"])
    out: list[Finding] = []
    inbound = df[df.amount >= min_amt][["id", "r", "s", "amount", "ts"]].rename(columns={"r": "acct"})
    outbound = df[["id", "s", "r", "amount", "ts"]].rename(columns={"s": "acct"}).sort_values("ts")
    out_by_acct = {a: g for a, g in outbound.groupby("acct")}
    used_out: set[int] = set()
    for row in inbound.sort_values("ts").itertuples(index=False):
        g = out_by_acct.get(row.acct)
        if g is None:
            continue
        lo, hi = np.searchsorted(g.ts.to_numpy(), [row.ts.to_datetime64(), (row.ts + window).to_datetime64()], side="right")
        cand = g.iloc[lo:hi]
        cand = cand[(cand.amount >= 0.2 * row.amount) & (~cand.id.isin(used_out))]
        if cand.empty:
            continue
        cum = cand.amount.cumsum().to_numpy()
        k = int(np.searchsorted(cum, ratio * row.amount))
        if k >= len(cand) or k >= 3 or cum[k] > 1.1 * row.amount:
            continue
        sel = cand.iloc[: k + 1]
        moved = float(cum[k])
        hours = (sel.ts.max() - row.ts).total_seconds() / 3600
        share = moved / row.amount
        conf = min(0.95, 0.45 + 0.3 * min(share, 1.0) + 0.2 * max(0.0, 1 - hours / (window.total_seconds() / 3600)))
        sev = "high" if hours <= 12 and row.amount >= 3 * min_amt else "medium"
        used_out.update(sel.id.tolist())
        out.append(Finding(
            "rapid_movement", sev, conf, f"{share:.0%} of {money(row.amount)} forwarded within {hours:.1f} h",
            [f"Received {money(row.amount)} at {_fmt_ts(row.ts)}",
             f"Sent out {money(moved)} ({share:.0%}) in {len(sel)} transfer(s) within {hours:.1f} hours",
             "Short holding time with near-complete onward transfer is typical of pass-through / mule accounts"],
            int(row.acct), [int(row.id), *sel.id.tolist()], sorted({int(row.acct), int(row.s), *map(int, sel.r)}),
            {"inbound": float(row.amount), "forwarded": moved, "hours": round(hours, 2), "share": round(share, 3)}))
    return out


# ------------------------------------------------------------------ 3. circular flows
def detect_circular(df: pd.DataFrame, p: dict) -> list[Finding]:
    """Temporal cycle search: money leaves an account and returns to it through 2..L other accounts,
    with each hop later than the previous one, within a time span, and with comparable amounts."""
    L = int(p["cycle_max_length"])
    span = pd.Timedelta(days=float(p["cycle_max_span_days"])).to_timedelta64()
    tol = float(p["cycle_amount_tolerance"])
    min_amt = float(p["rapid_min_amount"])
    d = df[df.amount >= min_amt].sort_values("ts")
    by_sender: dict[int, tuple[np.ndarray, pd.DataFrame]] = {}
    for a, g in d.groupby("s"):
        by_sender[a] = (g.ts.to_numpy(), g)
    found: dict[frozenset, list] = {}
    rows = list(d.itertuples(index=False))
    for e0 in rows:
        origin, a0, t0 = e0.s, e0.amount, e0.ts.to_datetime64()
        stack = [(e0.r, t0, [e0])]
        while stack:
            node, t_prev, path = stack.pop()
            if node not in by_sender:
                continue
            ts_arr, g = by_sender[node]
            lo, hi = np.searchsorted(ts_arr, [t_prev, t0 + span], side="right")
            for e in g.iloc[lo:hi].itertuples(index=False):
                if abs(e.amount - a0) > tol * a0:
                    continue
                visited = {x.s for x in path}
                if e.r == origin:
                    cyc = path + [e]
                    if len(cyc) >= 3 or (len(cyc) == 2 and abs(cyc[1].amount - a0) < 0.1 * a0):
                        found.setdefault(frozenset(x.id for x in cyc), cyc)
                elif e.r not in visited and len(path) + 1 < L:
                    stack.append((e.r, e.ts.to_datetime64(), path + [e]))
    out = []
    for cyc in found.values():
        amounts = [x.amount for x in cyc]
        days = (cyc[-1].ts - cyc[0].ts).total_seconds() / 86400
        decay = all(amounts[i + 1] <= amounts[i] * 1.01 for i in range(len(amounts) - 1))
        retained = amounts[-1] / amounts[0]
        conf = min(0.95, 0.55 + (0.15 if decay else 0) + (0.1 if days <= 7 else 0) + 0.05 * (len(cyc) - 2) + (0.05 if retained > 0.9 else 0))
        sev = "critical" if amounts[0] >= 5 * min_amt * 5 else ("high" if len(cyc) >= 3 else "medium")
        accts = [int(x.s) for x in cyc]
        out.append(Finding(
            "circular", sev, conf, f"{len(cyc)}-hop circular flow of {money(amounts[0])} returned to origin",
            [f"Funds left the origin account and returned after passing through {len(cyc) - 1} other account(s)",
             f"Duration {days:.1f} days; {retained:.0%} of the initial amount came back",
             "Amounts decrease steadily at each hop (consistent with layering fees)" if decay else "Hop amounts are comparable",
             ],
            accts[0], [int(x.id) for x in cyc], sorted(set(accts)),
            {"path_account_ids": accts + [accts[0]], "amounts": [round(a, 2) for a in amounts], "span_days": round(days, 2)}))
    return out


# ------------------------------------------------------------------ 4. unusual behaviour
def detect_unusual(df: pd.DataFrame, p: dict) -> list[Finding]:
    zmin = float(p["unusual_zscore"])
    min_hist = int(p["unusual_min_history"])
    out: list[Finding] = []
    d = df.copy()
    d["la"] = np.log1p(d.amount)
    d["hour"] = d.ts.dt.hour
    for sender, g in d.groupby("s"):
        if len(g) < min_hist:
            continue
        med = g.la.median()
        mad = max(float((g.la - med).abs().median()) * 1.4826, 0.25)
        z = (g.la - med) / mad
        night = g.hour.between(0, 4)
        night_share = float(night.mean())
        for r in g[z >= zmin].itertuples(index=False):
            zz = float((r.la - med) / mad)
            typical = float(np.expm1(med))
            out.append(Finding(
                "unusual_behavior", "medium" if zz >= zmin * 1.5 else "low", min(0.9, 0.4 + 0.05 * zz),
                f"Amount {money(r.amount)} far above this account's norm",
                [f"Amount is {r.amount / max(typical, 1):.1f}x the account's typical transaction ({money(typical)})",
                 f"Robust z-score {zz:.1f} against {len(g)} transactions by this sender"],
                int(sender), [int(r.id)], [int(sender), int(r.r)], {"zscore": round(zz, 2), "typical_amount": round(typical, 2)}))
        if night_share < 0.05:
            nt = g[night & (g.amount >= g.amount.median())]
            for r in nt.itertuples(index=False):
                out.append(Finding(
                    "unusual_behavior", "low", 0.45, f"Off-hours transfer of {money(r.amount)}",
                    [f"Sent at {r.ts.strftime('%H:%M')} UTC; under {night_share:.0%} of this account's {len(g)} transactions occur between 00:00 and 05:00",
                     "Amount is at or above the account's median"],
                    int(sender), [int(r.id)], [int(sender), int(r.r)], {"hour": int(r.hour)}))
    return out


# ------------------------------------------------------------------ 5. high velocity
def detect_velocity(df: pd.DataFrame, p: dict) -> list[Finding]:
    w = pd.Timedelta(minutes=float(p["velocity_window_minutes"])).to_timedelta64()
    min_n = int(p["velocity_min_count"])
    out = []
    for sender, g in df.sort_values("ts").groupby("s"):
        if len(g) < min_n:
            continue
        ts = g.ts.to_numpy()
        starts = np.searchsorted(ts, ts - w, side="left")
        counts = np.arange(len(ts)) - starts + 1
        hot = np.zeros(len(ts), bool)
        for i in np.where(counts >= min_n)[0]:
            hot[starts[i]: i + 1] = True
        if not hot.any():
            continue
        idx = np.where(hot)[0]
        groups = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
        for grp in groups:
            c = g.iloc[grp]
            mins = (c.ts.max() - c.ts.min()).total_seconds() / 60
            peak = int(counts[grp].max())
            out.append(Finding(
                "high_velocity", "high" if peak >= 2 * min_n else "medium", min(0.95, 0.55 + 0.03 * (peak - min_n)),
                f"{len(c)} transactions in {mins:.0f} minutes",
                [f"{len(c)} outgoing transactions within {mins:.0f} minutes (peak {peak} in a {int(p['velocity_window_minutes'])}-minute window)",
                 f"{c.r.nunique()} different recipients, total {money(float(c.amount.sum()))}",
                 f"This account's overall volume: {len(g)} transactions"],
                int(sender), c.id.tolist(), sorted({int(sender), *map(int, c.r.unique())}),
                {"count": len(c), "minutes": round(mins, 1), "recipients": int(c.r.nunique()), "peak_in_window": peak}))
    return out


# ------------------------------------------------------------------ 6. account networks
def detect_networks(df: pd.DataFrame, p: dict, holder: dict[int, str]) -> list[Finding]:
    out = []
    # (a) one IP address used by several differently-named account holders
    d = df[df.ip.notna() & (df.ip != "")]
    for ip, g in d.groupby("ip"):
        senders = g.s.unique()
        names = {holder.get(int(a), "").lower() for a in senders}
        if len(senders) >= 3 and len(names) >= 3:
            out.append(Finding(
                "account_network", "high" if len(names) >= 5 else "medium", min(0.9, 0.5 + 0.06 * len(names)),
                f"{len(senders)} accounts ({len(names)} different holders) transact from one IP address",
                [f"IP address {ip} was used to send from {len(senders)} accounts held by {len(names)} differently-named parties",
                 "Shared devices/connections across unrelated holders can indicate common control or a mule network",
                 "May also be benign (shared office, public network) - verify"],
                int(senders[0]), g.id.tolist(), sorted(map(int, senders)), {"ip_address": ip, "accounts": len(senders)}))
    # (b) accounts with highly overlapping *uncommon* counterparties
    deg = pd.concat([df.groupby("r").s.nunique(), df.groupby("s").r.nunique()], axis=1).fillna(0).max(axis=1)
    popular = set(deg[deg > 15].index)  # employers, merchants etc. are excluded from similarity
    cps: dict[int, set] = defaultdict(set)
    for s, r in df[["s", "r"]].itertuples(index=False):
        if r not in popular:
            cps[int(s)].add(int(r))
        if s not in popular:
            cps[int(r)].add(int(s))
    min_shared, min_j = int(p["network_min_shared_counterparties"]), float(p["network_min_jaccard"])
    inverted: dict[int, list[int]] = defaultdict(list)
    for a, cs in cps.items():
        for c in cs:
            inverted[c].append(a)
    pairs: dict[tuple, int] = defaultdict(int)
    for c, accts in inverted.items():
        if len(accts) > 40:
            continue
        for i in range(len(accts)):
            for j in range(i + 1, len(accts)):
                pairs[(min(accts[i], accts[j]), max(accts[i], accts[j]))] += 1
    for (a, b), shared in pairs.items():
        if shared < min_shared:
            continue
        j = shared / len(cps[a] | cps[b])
        if j < min_j:
            continue
        common = cps[a] & cps[b]
        tx = df[((df.s.isin([a, b])) & (df.r.isin(common))) | ((df.r.isin([a, b])) & (df.s.isin(common)))]
        out.append(Finding(
            "account_network", "medium", min(0.85, 0.4 + 0.4 * j),
            f"Two accounts share {shared} uncommon counterparties",
            [f"Jaccard similarity {j:.2f} of counterparty sets ({shared} shared, excluding high-volume counterparties)",
             "Accounts operating through the same set of counterparties may be under common control"],
            a, tx.id.tolist()[:200], sorted({a, b, *common}), {"pair": [a, b], "shared": shared, "jaccard": round(j, 3)}))
    return out


# ------------------------------------------------------------------ 7. dormant reactivation
def detect_dormant(df: pd.DataFrame, p: dict) -> list[Finding]:
    gap = pd.Timedelta(days=float(p["dormant_days"]))
    min_amt = float(p["dormant_min_amount"])
    acts = pd.concat([df[["id", "s", "amount", "ts"]].rename(columns={"s": "acct"}),
                      df[["id", "r", "amount", "ts"]].rename(columns={"r": "acct"})]).sort_values(["acct", "ts"])
    acts["prev"] = acts.groupby("acct").ts.shift(1)
    acts["gap"] = acts.ts - acts.prev
    out = []
    for r in acts[(acts.gap >= gap) & (acts.amount >= min_amt)].itertuples(index=False):
        follow = acts[(acts.acct == r.acct) & (acts.ts >= r.ts) & (acts.ts <= r.ts + pd.Timedelta(days=14)) & (acts.amount >= min_amt / 2)]
        total = float(follow.amount.sum())
        days = r.gap.total_seconds() / 86400
        out.append(Finding(
            "dormant_reactivation", "high" if total >= 3 * min_amt else "medium", min(0.92, 0.5 + days / 1000 + 0.05 * len(follow)),
            f"Account reactivated after {days:.0f} days with {money(total)} in activity",
            [f"No activity for {days:.0f} days (since {pd.Timestamp(r.prev).strftime('%Y-%m-%d')})",
             f"Reactivated with a {money(r.amount)} transaction on {_fmt_ts(r.ts)}",
             f"{len(follow)} large transaction(s) totalling {money(total)} within 14 days of reactivation"],
            int(r.acct), sorted(set(follow.id.tolist())), [int(r.acct)], {"dormant_days": round(days, 1), "total": total}))
    return out
