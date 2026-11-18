"""Entity resolution & relationship linking.

Produces *suggested* EntityLinks with explainable signals and a combined confidence. Nothing is merged
automatically: an investigator must verify a 'possible_duplicate' link before a merge is allowed, and name
similarity alone can never produce more than a low-confidence suggestion.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, Entity, EntityLink, Transaction, utcnow

W = {"exact_name": 0.35, "similar_name": 0.2, "initial_name": 0.15, "shared_ip": 0.4, "shared_reference": 0.25,
     "shared_contact": 0.5, "shared_account_identifier": 0.6, "repeated_transfers": 0.3, "common_counterparties": 0.2}
IDENTITY_SIGNALS = {"shared_ip", "shared_contact", "shared_account_identifier", "shared_reference"}
NAME_SIGNALS = {"exact_name", "similar_name", "initial_name"}
BEHAVIOUR_SIGNALS = {"repeated_transfers", "common_counterparties"}
MIN_CONFIDENCE = 0.3


def _initial_match(a: str, b: str) -> bool:
    ta, tb = a.split(), b.split()
    if len(ta) < 2 or len(tb) < 2 or ta[-1] != tb[-1]:
        return False
    fa, fb = ta[0], tb[0]
    return (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb))


def compute_links(db: Session) -> dict:
    ents = {e.id: e for e in db.scalars(select(Entity).where(Entity.merged_into_id.is_(None)))}
    acct_ent = {a: e for a, e in db.execute(select(Account.id, Account.entity_id)) if e in ents}
    signals: dict[tuple[int, int], list[dict]] = defaultdict(list)

    def add(a, b, sig, detail):
        if a == b or a is None or b is None:
            return
        key = (min(a, b), max(a, b))
        if not any(s["signal"] == sig for s in signals[key]):
            signals[key].append({"signal": sig, "weight": W[sig], "detail": detail})

    # --- names (blocked by surname token to stay sub-quadratic)
    blocks = defaultdict(list)
    for e in ents.values():
        toks = e.normalized_name.split()
        if toks:
            blocks[(e.entity_type, toks[-1])].append(e)
    for blk in blocks.values():
        if len(blk) > 400:
            continue
        for x, y in combinations(blk, 2):
            if not x.normalized_name or not y.normalized_name:
                continue
            if x.normalized_name == y.normalized_name:
                add(x.id, y.id, "exact_name", f"Identical name '{x.name}'")
            elif _initial_match(x.normalized_name, y.normalized_name):
                add(x.id, y.id, "initial_name", f"'{x.name}' / '{y.name}' share surname and first initial")
            else:
                sc = fuzz.token_sort_ratio(x.normalized_name, y.normalized_name)
                if sc >= 88:
                    add(x.id, y.id, "similar_name", f"Name similarity {sc:.0f}% ('{x.name}' / '{y.name}')")

    # --- shared contact info / identifiers held in entity attributes (emails, phones, national ids)
    contact_idx = defaultdict(set)
    for e in ents.values():
        for k in ("emails", "phones", "identifiers"):
            for v in (e.attributes or {}).get(k, []) or []:
                contact_idx[(k, str(v).strip().lower())].add(e.id)
    for (k, v), ids in contact_idx.items():
        if 1 < len(ids) <= 10:
            for a, b in combinations(sorted(ids), 2):
                add(a, b, "shared_account_identifier" if k == "identifiers" else "shared_contact", f"Shared {k[:-1]} {v}")

    # --- shared IP addresses (as sender), excluding widely shared IPs
    ip_ents = defaultdict(set)
    for ip, acct in db.execute(select(Transaction.ip_address, Transaction.sender_account_id).where(Transaction.ip_address.isnot(None)).distinct()):
        if acct in acct_ent:
            ip_ents[ip].add(acct_ent[acct])
    for ip, ids in ip_ents.items():
        if 1 < len(ids) <= 10:
            for a, b in combinations(sorted(ids), 2):
                add(a, b, "shared_ip", f"Both transacted from IP address {ip}")

    # --- shared, uncommon transaction references
    ref_ents = defaultdict(set)
    ref_counts = dict(db.execute(select(Transaction.reference, func.count()).where(Transaction.reference.isnot(None)).group_by(Transaction.reference)).all())
    for ref, s, r in db.execute(select(Transaction.reference, Transaction.sender_account_id, Transaction.receiver_account_id).where(Transaction.reference.isnot(None))):
        if ref_counts.get(ref, 0) <= 6:
            for acct in (s,):
                if acct in acct_ent:
                    ref_ents[ref].add(acct_ent[acct])
    for ref, ids in ref_ents.items():
        if 1 < len(ids) <= 4:
            for a, b in combinations(sorted(ids), 2):
                add(a, b, "shared_reference", f"Both used transaction reference '{ref}'")

    # --- repeated direct transfer relationships and common counterparties
    pair_counts = defaultdict(int)
    cps = defaultdict(set)
    deg = defaultdict(set)
    rows = db.execute(select(Transaction.sender_account_id, Transaction.receiver_account_id, func.count()).group_by(
        Transaction.sender_account_id, Transaction.receiver_account_id)).all()
    for s, r, n in rows:
        es, er = acct_ent.get(s), acct_ent.get(r)
        if es and er and es != er:
            pair_counts[(min(es, er), max(es, er))] += n
            deg[er].add(es)
            deg[es].add(er)
    for (a, b), n in pair_counts.items():
        if n >= 3:
            add(a, b, "repeated_transfers", f"{n} transfers between these parties")
    for s, r, _ in rows:
        es, er = acct_ent.get(s), acct_ent.get(r)
        if es and er and es != er:
            if len(deg[er]) <= 15:
                cps[es].add(er)
            if len(deg[es]) <= 15:
                cps[er].add(es)
    inv = defaultdict(list)
    for e, cs in cps.items():
        for c in cs:
            inv[c].append(e)
    shared = defaultdict(int)
    for c, es in inv.items():
        if len(es) <= 30:
            for a, b in combinations(sorted(es), 2):
                shared[(a, b)] += 1
    for (a, b), k in shared.items():
        j = k / max(len(cps[a] | cps[b]), 1)
        if k >= 3 and j >= 0.5:
            add(a, b, "common_counterparties", f"{k} common uncommon counterparties (Jaccard {j:.2f})")

    # --- persist: refresh 'suggested' links only; investigator decisions are preserved
    existing = {(l.entity_a_id, l.entity_b_id): l for l in db.scalars(select(EntityLink))}
    created = updated = 0
    keep = set()
    for (a, b), sigs in signals.items():
        conf = 1.0
        for s in sigs:
            conf *= 1 - s["weight"]
        conf = round(1 - conf, 3)
        names = {s["signal"] for s in sigs}
        if names <= NAME_SIGNALS:
            conf = min(conf, 0.35)  # name similarity alone stays a weak suggestion
        if conf < MIN_CONFIDENCE or names <= BEHAVIOUR_SIGNALS:
            continue  # purely behavioural relationships (e.g. payroll, rent) are too common to suggest on their own
        ltype = "possible_duplicate" if (names & NAME_SIGNALS) and (names & IDENTITY_SIGNALS or "exact_name" in names) else "related"
        keep.add((a, b))
        l = existing.get((a, b))
        if l is None:
            db.add(EntityLink(entity_a_id=a, entity_b_id=b, link_type=ltype, confidence=conf, signals=sigs))
            created += 1
        elif l.status == "suggested":
            l.link_type, l.confidence, l.signals = ltype, conf, sigs
            updated += 1
    removed = 0
    for k, l in existing.items():
        if k not in keep and l.status == "suggested":
            db.delete(l)
            removed += 1
    db.commit()
    return {"suggested_created": created, "suggested_updated": updated, "stale_removed": removed, "entities": len(ents)}


def merge_entities(db: Session, link: EntityLink, keep_id: int, user_id: int) -> Entity:
    """Merge two entities linked by an investigator-*verified* possible_duplicate link."""
    if link.status != "verified" or link.link_type != "possible_duplicate":
        raise ValueError("Only verified 'possible duplicate' links can be merged")
    if keep_id not in (link.entity_a_id, link.entity_b_id):
        raise ValueError("keep_id must be one of the linked entities")
    drop_id = link.entity_b_id if keep_id == link.entity_a_id else link.entity_a_id
    keep, drop = db.get(Entity, keep_id), db.get(Entity, drop_id)
    if drop.merged_into_id:
        raise ValueError("Entity already merged")
    for a in db.scalars(select(Account).where(Account.entity_id == drop_id)):
        a.entity_id = keep_id
    attrs = dict(keep.attributes or {})
    aliases = set(attrs.get("aliases", [])) | {drop.name}
    attrs["aliases"] = sorted(aliases - {keep.name})
    attrs.setdefault("merged_from", []).append({"entity_id": drop_id, "name": drop.name, "by_user": user_id, "at": utcnow().isoformat()})
    keep.attributes = attrs
    keep.risk_score = max(keep.risk_score, drop.risk_score)
    drop.merged_into_id = keep_id
    return keep
