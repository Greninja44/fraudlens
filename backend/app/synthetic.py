"""Synthetic financial transaction dataset generator.

All people, companies, banks, accounts and IP addresses are fictitious (IPs use RFC 5737 documentation ranges).
The output contains ground-truth columns (`is_fraud`, `fraud_pattern`) so detection quality can be measured.
These labels are *only* for evaluation; the detection engine never reads them.

Usage:  python -m app.synthetic --out ../datasets [--seed 42]
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

UTC = timezone.utc
START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)
BANKS = ["Northbridge Bank", "Meridian Trust", "Harbor Federal", "Crestline Credit Union", "Atlas Savings"]
FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth", "William",
         "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy",
         "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Sandra", "Steven", "Ashley", "Paul", "Emily", "Andrew", "Donna",
         "Joshua", "Michelle", "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Melissa", "George", "Deborah", "Priya",
         "Arjun", "Wei", "Mei", "Omar", "Fatima", "Lucas", "Sofia", "Mateo", "Elena", "Kofi", "Amara", "Yuki", "Hiro"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
        "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young",
        "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Patel", "Chen", "Okafor", "Kim"]
BIZ_A = ["Summit", "Granite", "Pioneer", "Riverbend", "Cedar", "Lakeside", "Beacon", "Harvest", "Ironwood", "Maple",
         "Northstar", "Redwood", "Sterling", "Evergreen", "Copper", "Falcon", "Horizon", "Juniper", "Keystone", "Oakridge"]
BIZ_B = ["Grocers", "Hardware", "Dental Clinic", "Auto Repair", "Bakery", "Logistics", "Consulting", "Pharmacy",
         "Fitness", "Electric", "Property Management", "Software", "Catering", "Printing", "Supply Co", "Realty"]
BIZ_SFX = ["LLC", "Inc", "Ltd", "Co", "Group"]


@dataclass
class Acct:
    number: str
    name: str
    bank: str
    kind: str  # individual|business
    ip: str
    tags: set = field(default_factory=set)


class Generator:
    def __init__(self, seed: int = 42):
        self.r = random.Random(seed)
        self.rows: list[dict] = []
        self.accounts: list[Acct] = []
        self._acct_seq = 0
        self._txn_seq = 0
        self._ip_seq = 0

    # ---------- helpers
    def ip(self) -> str:
        self._ip_seq += 1
        blocks = ["192.0.2", "198.51.100", "203.0.113"]
        n = self._ip_seq
        return f"{blocks[(n // 250) % 3]}.{n % 250 + 1}"

    def new_account(self, name: str, kind: str, bank: str | None = None, ip: str | None = None) -> Acct:
        self._acct_seq += 1
        a = Acct(f"AC{4000000000 + self._acct_seq * 7919:010d}", name, bank or self.r.choice(BANKS), kind, ip or self.ip())
        self.accounts.append(a)
        return a

    def ts(self, start=START, end=END, business_hours=True) -> datetime:
        t = start + timedelta(seconds=self.r.randint(0, int((end - start).total_seconds())))
        if business_hours:
            t = t.replace(hour=self.r.randint(8, 19), minute=self.r.randint(0, 59), second=self.r.randint(0, 59))
        return t

    def add(self, s: Acct, d: Acct, amount: float, t: datetime, ttype: str, ref: str | None = None,
            pattern: str | None = None, ip: str | None = None, status: str = "completed") -> None:
        self._txn_seq += 1
        self.rows.append({
            "transaction_id": f"TXN{self._txn_seq:07d}",
            "sender_account": s.number, "sender_name": s.name, "sender_bank": s.bank,
            "receiver_account": d.number, "receiver_name": d.name, "receiver_bank": d.bank,
            "amount": round(amount, 2), "currency": "USD",
            "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "transaction_type": ttype,
            "reference": ref or f"REF{self.r.randint(10**8, 10**9 - 1)}",
            "ip_address": ip if ip is not None else s.ip,
            "status": status,
            "is_fraud": 1 if pattern else 0,
            "fraud_pattern": pattern or "",
        })

    # ---------- population
    def build_population(self, n_ind=320, n_biz=70):
        used = set()
        self.people: list[Acct] = []
        while len(self.people) < n_ind:
            name = f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"
            if name in used and self.r.random() > 0.08:  # a few genuine namesakes (different people!)
                continue
            used.add(name)
            self.people.append(self.new_account(name, "individual"))
        # ~8% of people hold a second account at another bank (same person, same IP) - entity-linking signal
        self.second_accounts: list[tuple[Acct, Acct]] = []
        for p in self.r.sample(self.people, int(n_ind * 0.08)):
            second = self.new_account(p.name, "individual", bank=self.r.choice([b for b in BANKS if b != p.bank]), ip=p.ip)
            second.tags.add("second_account")
            self.second_accounts.append((p, second))
        names = set()
        self.biz: list[Acct] = []
        while len(self.biz) < n_biz:
            name = f"{self.r.choice(BIZ_A)} {self.r.choice(BIZ_B)} {self.r.choice(BIZ_SFX)}"
            if name in names:
                continue
            names.add(name)
            self.biz.append(self.new_account(name, "business"))
        self.merchants = [b for b in self.biz if any(k in b.name for k in ("Grocers", "Bakery", "Pharmacy", "Hardware", "Fitness", "Catering", "Auto Repair", "Dental Clinic"))] or self.biz[:10]
        self.landlords = self.r.sample(self.people, 25) + [b for b in self.biz if "Property" in b.name or "Realty" in b.name]

    # ---------- normal behaviour
    def normal_activity(self):
        r = self.r
        employers = {p.number: r.choice(self.biz) for p in self.people}
        rent = {p.number: (r.choice(self.landlords), r.uniform(800, 2600)) for p in self.people if r.random() < 0.65}
        salary = {p.number: r.uniform(2600, 8800) for p in self.people}
        for month in range(1, 13):
            mstart = datetime(2025, month, 1, tzinfo=UTC)
            for p in self.people:
                if "dormant" in p.tags or "fraud_only" in p.tags:
                    continue
                pay_day = min(28, 25 + r.randint(0, 3))
                self.add(employers[p.number], p, salary[p.number] * r.uniform(0.98, 1.02),
                         mstart.replace(day=pay_day, hour=r.randint(6, 9), minute=r.randint(0, 59)), "payroll",
                         ref=f"PAY-{month:02d}-{employers[p.number].number[-4:]}", ip=employers[p.number].ip)
                if p.number in rent and rent[p.number][0].number != p.number:
                    landlord, amt = rent[p.number]
                    self.add(p, landlord, amt, mstart.replace(day=r.randint(1, 5), hour=r.randint(7, 22), minute=r.randint(0, 59)),
                             "transfer", ref=f"RENT-{month:02d}")
                for _ in range(r.choice([0, 1, 1, 2, 2, 3])):  # purchases
                    mend = (mstart + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
                    self.add(p, r.choice(self.merchants), r.lognormvariate(4.0, 0.9), self.ts(mstart, mend), "payment")
                if r.random() < 0.25:  # P2P
                    mend = (mstart + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
                    self.add(p, r.choice([q for q in self.people if q is not p]), r.uniform(20, 600), self.ts(mstart, mend, business_hours=False) .replace(hour=r.randint(7, 23)), "p2p")
            for p, second in self.second_accounts:  # same person moving savings to their other bank, spending from it
                if r.random() < 0.6:
                    self.add(p, second, r.uniform(200, 1500), mstart.replace(day=r.randint(26, 28), hour=r.randint(9, 21), minute=r.randint(0, 59)),
                             "transfer", ref="SAVINGS")
                if r.random() < 0.5:
                    mend = (mstart + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
                    self.add(second, r.choice(self.merchants), r.lognormvariate(4.0, 0.8), self.ts(mstart, mend), "payment")
            for b in self.biz:  # B2B invoices
                for _ in range(r.choice([1, 1, 2])):
                    supplier = r.choice([x for x in self.biz if x is not b])
                    amt = r.lognormvariate(8.6, 0.8)  # median ~5.4k, sometimes > 10k
                    if r.random() < 0.04:
                        amt = r.uniform(9000, 9950)  # legitimate near-threshold invoices (realistic false-positive source)
                    mend = (mstart + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
                    self.add(b, supplier, amt, self.ts(mstart, mend), "wire", ref=f"INV-{r.randint(10000, 99999)}")
        # a little legitimate night-time activity
        for p in r.sample(self.people, 30):
            t = self.ts(business_hours=False).replace(hour=r.randint(0, 4))
            self.add(p, r.choice(self.merchants), r.uniform(15, 120), t, "payment")
        # a few failed transactions
        for p in r.sample(self.people, 40):
            self.add(p, r.choice(self.merchants), r.uniform(20, 300), self.ts(), "payment", status="failed")

    # ---------- suspicious patterns (ground truth)
    def fraud_patterns(self):
        r = self.r
        # Featured demo ring ("Operation Ledger") - shell companies + a controller sharing one IP address.
        ring_ip = "203.0.113.250"
        victor = self.new_account("Victor Hale", "individual", "Harbor Federal", ip=ring_ip)
        victor2 = self.new_account("V. Hale", "individual", "Atlas Savings", ip=ring_ip)  # alias account
        shells = [self.new_account(n, "business", b, ip=ring_ip) for n, b in [
            ("Bluewater Trading LLC", "Meridian Trust"), ("Orion Logistics Ltd", "Northbridge Bank"),
            ("Kestrel Imports Inc", "Crestline Credit Union"), ("Silverline Consulting LLC", "Atlas Savings")]]
        for a in [victor, victor2, *shells]:
            a.tags.add("fraud_only")
        # small "cover" activity so the shells look alive
        for s in shells:
            for _ in range(6):
                self.add(r.choice(self.biz), s, r.uniform(800, 4000), self.ts(START, datetime(2025, 9, 1, tzinfo=UTC)), "wire",
                         ref=f"INV-{r.randint(10000, 99999)}", ip=None)
        # (a) structuring into Victor's account: cash-like deposits just under 10k from several mules
        mules = r.sample(self.people, 5)
        base = datetime(2025, 9, 8, 10, tzinfo=UTC)
        for i, m in enumerate(mules):
            for k in range(2):
                self.add(m, victor, r.uniform(8600, 9900), base + timedelta(days=r.randint(0, 4), hours=r.randint(0, 9), minutes=r.randint(0, 59)),
                         "deposit", pattern="smurfing", ip=ring_ip if k == 0 and i < 2 else None)
        # (b) Victor layers funds through the shells in a cycle back to his alias account, then to himself
        t = datetime(2025, 9, 15, 9, 30, tzinfo=UTC)
        amt = 84000.0
        chain = [victor, shells[0], shells[1], shells[2], shells[3], victor2]
        for i in range(len(chain) - 1):
            self.add(chain[i], chain[i + 1], amt, t, "wire", ref=f"CONSULT-{2025}-{i + 11}", pattern="circular")
            t += timedelta(hours=r.randint(5, 30))
            amt *= r.uniform(0.97, 0.995)
        self.add(victor2, victor, amt * 0.99, t, "transfer", ref="LOAN REPAY", pattern="circular")
        # (c) the same ring repeats two months later (rapid pass-through)
        t = datetime(2025, 11, 3, 11, tzinfo=UTC)
        src = r.choice(self.biz)
        self.add(src, shells[0], 46000, t, "wire", ref="INV-SHELL-77", pattern="rapid_movement", ip=src.ip)
        self.add(shells[0], shells[3], 45100, t + timedelta(hours=3), "wire", pattern="rapid_movement")
        self.add(shells[3], victor, 44200, t + timedelta(hours=7), "wire", pattern="rapid_movement")
        self.featured = {"controller": victor.number, "alias": victor2.number, "shells": [s.number for s in shells]}

        # Additional structuring subjects (self-structuring into a second account)
        for _ in range(6):
            s = r.choice(self.people)
            dest = self.new_account(s.name, "individual", r.choice([b for b in BANKS if b != s.bank]), ip=s.ip)
            dest.tags.add("fraud_only")
            t0 = self.ts(datetime(2025, 3, 1, tzinfo=UTC), datetime(2025, 11, 1, tzinfo=UTC))
            for _ in range(r.randint(4, 7)):
                self.add(s, dest, r.uniform(8500, 9950), t0 + timedelta(days=r.uniform(0, 6)), "transfer", pattern="structuring")

        # Smurfing rings: several mules -> one collector, shared device IP for some mules
        for _ in range(3):
            collector = self.new_account(f"{r.choice(FIRST)} {r.choice(LAST)}", "individual")
            collector.tags.add("fraud_only")
            ring_mules = r.sample(self.people, r.randint(4, 6))
            shared = self.ip()
            t0 = self.ts(datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC))
            for m in ring_mules:
                self.add(m, collector, r.uniform(8000, 9800), t0 + timedelta(hours=r.uniform(0, 72)), "deposit",
                         pattern="smurfing", ip=shared if r.random() < 0.6 else None)

        # Additional circular flows among otherwise-normal businesses
        for _ in range(4):
            k = r.randint(3, 5)
            nodes = r.sample(self.biz, k)
            t = self.ts(datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 11, 15, tzinfo=UTC))
            amt = r.uniform(25000, 90000)
            for i in range(k):
                self.add(nodes[i], nodes[(i + 1) % k], amt, t, "wire", ref=f"SVC-{r.randint(1000, 9999)}", pattern="circular")
                t += timedelta(hours=r.uniform(4, 60))
                amt *= r.uniform(0.96, 0.995)

        # Money mules: rapid pass-through of incoming funds
        for _ in range(8):
            mule = self.new_account(f"{r.choice(FIRST)} {r.choice(LAST)}", "individual")
            mule.tags.add("fraud_only")
            for _ in range(r.randint(2, 3)):
                src, dst = r.choice(self.people + self.biz), self.new_account(f"{r.choice(FIRST)} {r.choice(LAST)}", "individual")
                dst.tags.add("fraud_only")
                amt = r.uniform(4000, 26000)
                t = self.ts(datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 12, 20, tzinfo=UTC))
                self.add(src, mule, amt, t, "transfer", pattern="rapid_movement", ip=src.ip)
                self.add(mule, dst, amt * r.uniform(0.9, 0.98), t + timedelta(hours=r.uniform(1, 20)), "transfer", pattern="rapid_movement")

        # High-velocity bursts
        for _ in range(5):
            s = r.choice(self.people)
            t = self.ts(datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 12, 20, tzinfo=UTC), business_hours=False)
            for _ in range(r.randint(14, 28)):
                t += timedelta(seconds=r.randint(30, 160))
                self.add(s, r.choice([q for q in self.people + self.merchants if q is not s]), r.uniform(60, 950), t, "p2p", pattern="high_velocity")

        # Dormant account reactivation
        for _ in range(6):
            d = self.new_account(f"{r.choice(FIRST)} {r.choice(LAST)}", "individual")
            d.tags.add("dormant")
            for _ in range(3):  # light early activity
                self.add(r.choice(self.people), d, r.uniform(50, 400), self.ts(START, datetime(2025, 2, 15, tzinfo=UTC)), "p2p")
            t = self.ts(datetime(2025, 10, 1, tzinfo=UTC), datetime(2025, 12, 15, tzinfo=UTC), business_hours=False)
            funder = r.choice(self.biz)
            self.add(funder, d, r.uniform(20000, 60000), t, "wire", pattern="dormant_reactivation", ip=funder.ip)
            for _ in range(r.randint(1, 3)):
                t += timedelta(hours=r.uniform(2, 30))
                self.add(d, r.choice(self.people), r.uniform(6000, 18000), t, "transfer", pattern="dormant_reactivation")

    def generate(self) -> pd.DataFrame:
        self.build_population()
        self.normal_activity()
        self.fraud_patterns()
        df = pd.DataFrame(self.rows).sort_values("timestamp").reset_index(drop=True)
        return df


def messy_export(df: pd.DataFrame, seed: int = 7, n: int = 600) -> pd.DataFrame:
    """A 'bank export' with different headers, formats, missing values, duplicates and invalid rows -
    for exercising automatic column mapping and validation."""
    r = random.Random(seed)
    sample = df.sample(n=n, random_state=seed).copy()
    sample["transaction_id"] = ["BX-" + t[3:] for t in sample["transaction_id"]]
    out = pd.DataFrame({
        "Txn Ref No": sample["transaction_id"],
        "Value Date": pd.to_datetime(sample["timestamp"]).dt.strftime("%d/%m/%Y %H:%M"),
        "From A/C": sample["sender_account"],
        "Remitter": sample["sender_name"],
        "To A/C": sample["receiver_account"],
        "Beneficiary": sample["receiver_name"],
        "Amt": [f"${a:,.2f}" for a in sample["amount"]],
        "Ccy": sample["currency"],
        "Channel": sample["transaction_type"].str.upper(),
        "Narration": sample["reference"],
        "Bank Name": sample["sender_bank"],
        "Client IP": sample["ip_address"],
        "Txn Status": sample["status"].str.title(),
    }).reset_index(drop=True)
    for i in r.sample(range(n), 25):
        out.loc[i, "Beneficiary"] = None
    for i in r.sample(range(n), 12):
        out.loc[i, "Client IP"] = ""
    bad = r.sample(range(n), 8)
    out.loc[bad[0], "Amt"] = "-$450.00"
    out.loc[bad[1], "Amt"] = "n/a"
    out.loc[bad[2], "Value Date"] = "31/02/2025 10:00"
    out.loc[bad[3], "From A/C"] = None
    out.loc[bad[4], "To A/C"] = out.loc[bad[4], "From A/C"]  # self-transfer
    out.loc[bad[5], "Value Date"] = "yesterday"
    dups = out.sample(n=15, random_state=seed + 1)
    return pd.concat([out, dups], ignore_index=True)


def write_all(out_dir: Path, seed: int = 42) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    g = Generator(seed)
    df = g.generate()
    df.to_csv(out_dir / "synthetic_transactions.csv", index=False)
    df.to_json(out_dir / "synthetic_transactions.json", orient="records", indent=0)
    small = df[df["timestamp"] >= "2025-09-01"].reset_index(drop=True)
    small.to_excel(out_dir / "synthetic_transactions_q4.xlsx", index=False, sheet_name="transactions")
    messy = messy_export(df)
    messy.to_csv(out_dir / "messy_bank_export.csv", index=False)
    messy.to_excel(out_dir / "messy_bank_export.xlsx", index=False)
    meta = {
        "seed": seed, "transactions": int(len(df)), "suspicious_labelled": int(df["is_fraud"].sum()),
        "patterns": {k: int(v) for k, v in df[df["is_fraud"] == 1]["fraud_pattern"].value_counts().items()},
        "accounts": len({*df["sender_account"], *df["receiver_account"]}),
        "featured_ring": g.featured,
        "files": ["synthetic_transactions.csv", "synthetic_transactions.json", "synthetic_transactions_q4.xlsx",
                  "messy_bank_export.csv", "messy_bank_export.xlsx"],
        "note": "Synthetic data. is_fraud/fraud_pattern are ground-truth labels for evaluation only.",
    }
    (out_dir / "dataset_card.json").write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "datasets")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    print(json.dumps(write_all(a.out, a.seed), indent=2))
