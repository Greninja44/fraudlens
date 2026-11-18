"""Transaction ingestion: file reading, automatic column mapping, validation, normalisation,
duplicate detection and bulk loading.

Flow:  stage_upload() -> preview (suggested mapping + sample)  ->  run_import(batch) in the background.
The original uploaded file is kept unchanged in object storage (imports/<sha256>/<filename>).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta  # noqa: F401
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.models import Account, Entity, ImportBatch, Transaction, utcnow

# ---------------------------------------------------------------- column mapping
CANONICAL_FIELDS: dict[str, dict] = {
    "txn_id": {"label": "Transaction ID", "required": False,
               "synonyms": ["transactionid", "txnid", "txnrefno", "transactionref", "id", "tranid", "transid", "txnno", "transactionno", "transactionnumber"]},
    "sender_account": {"label": "Sender account", "required": True,
                       "synonyms": ["senderaccount", "fromaccount", "fromac", "debitaccount", "sourceaccount", "originaccount", "payeraccount", "remitteraccount", "sender", "accountfrom", "fromacct", "senderacct"]},
    "receiver_account": {"label": "Receiver account", "required": True,
                         "synonyms": ["receiveraccount", "toaccount", "toac", "creditaccount", "destinationaccount", "beneficiaryaccount", "payeeaccount", "receiver", "accountto", "toacct", "receiveracct"]},
    "sender_name": {"label": "Sender name", "required": False,
                    "synonyms": ["sendername", "fromname", "remitter", "remittername", "payer", "payername", "originator", "debtorname"]},
    "receiver_name": {"label": "Receiver name", "required": False,
                      "synonyms": ["receivername", "toname", "beneficiary", "beneficiaryname", "payee", "payeename", "creditorname"]},
    "amount": {"label": "Amount", "required": True,
               "synonyms": ["amount", "amt", "value", "transactionamount", "txnamount", "sum", "amountusd"]},
    "currency": {"label": "Currency", "required": False, "synonyms": ["currency", "ccy", "cur", "currencycode"]},
    "timestamp": {"label": "Timestamp", "required": True,
                  "synonyms": ["timestamp", "datetime", "date", "valuedate", "transactiondate", "txndate", "postingdate", "time", "createdat", "bookingdate"]},
    "txn_type": {"label": "Transaction type", "required": False,
                 "synonyms": ["transactiontype", "type", "txntype", "channel", "method", "paymenttype"]},
    "bank": {"label": "Bank / institution", "required": False,
             "synonyms": ["bank", "bankname", "senderbank", "institution", "financialinstitution", "frombank"]},
    "receiver_bank": {"label": "Receiver bank", "required": False, "synonyms": ["receiverbank", "tobank", "beneficiarybank"]},
    "reference": {"label": "Reference", "required": False,
                  "synonyms": ["reference", "ref", "narration", "description", "memo", "remarks", "paymentreference", "details"]},
    "ip_address": {"label": "IP address", "required": False, "synonyms": ["ipaddress", "ip", "clientip", "sourceip", "deviceip"]},
    "status": {"label": "Status", "required": False, "synonyms": ["status", "txnstatus", "transactionstatus", "state"]},
    "ground_truth_label": {"label": "Ground-truth label (synthetic only)", "required": False,
                           "synonyms": ["isfraud", "fraudlabel", "label", "groundtruth", "issuspicious"]},
    "ground_truth_pattern": {"label": "Ground-truth pattern (synthetic only)", "required": False,
                             "synonyms": ["fraudpattern", "fraudtype", "patternlabel", "typology"]},
}
REQUIRED = [k for k, v in CANONICAL_FIELDS.items() if v["required"]]


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def suggest_mapping(columns: list[str]) -> dict[str, dict]:
    """Return {canonical_field: {"column": source_column, "confidence": 0-1, "method": exact|fuzzy}}."""
    normed = {c: _norm_header(c) for c in columns}
    mapping: dict[str, dict] = {}
    used: set[str] = set()
    # pass 1: exact synonym matches
    for field, spec in CANONICAL_FIELDS.items():
        for col, n in normed.items():
            if col not in used and n in spec["synonyms"]:
                mapping[field] = {"column": col, "confidence": 1.0, "method": "exact"}
                used.add(col)
                break
    # pass 2: fuzzy matches - assign globally best (field, column) pairs first so one greedy field
    # cannot steal a column that matches another field better
    cands = []
    for field, spec in CANONICAL_FIELDS.items():
        if field in mapping:
            continue
        for col, n in normed.items():
            if col in used or not n:
                continue
            score = max(fuzz.ratio(n, s) for s in spec["synonyms"])
            if score >= 85:
                cands.append((score, field, col))
    for score, field, col in sorted(cands, reverse=True):
        if field not in mapping and col not in used:
            mapping[field] = {"column": col, "confidence": round(score / 100, 2), "method": "fuzzy"}
            used.add(col)
    return mapping


# ---------------------------------------------------------------- file reading
SUPPORTED = {".csv": "csv", ".xlsx": "xlsx", ".json": "json"}


class IngestionError(Exception):
    pass


def detect_file_type(filename: str, head: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise IngestionError(f"Unsupported file type '{ext}'. Supported: CSV, XLSX, JSON")
    ftype = SUPPORTED[ext]
    if ftype == "xlsx" and not head.startswith(b"PK"):
        raise IngestionError("File has .xlsx extension but is not a valid Excel workbook")
    if ftype in ("csv", "json"):
        if b"\x00" in head[:4096]:
            raise IngestionError("File appears to be binary, not text")
        if ftype == "json" and head.lstrip()[:1] not in (b"[", b"{"):
            raise IngestionError("JSON file must contain an array of objects (or {\"transactions\": [...]})")
    return ftype


def iter_frames(fh, ftype: str, chunksize: int = 20000) -> Iterator[pd.DataFrame]:
    """Yield DataFrames of string values. CSV is streamed in chunks to bound memory use."""
    if ftype == "csv":
        yield from pd.read_csv(fh, dtype=str, keep_default_na=False, chunksize=chunksize, encoding_errors="replace")
    elif ftype == "xlsx":
        df = pd.read_excel(fh, dtype=str, engine="openpyxl").fillna("")
        for i in range(0, len(df), chunksize):
            yield df.iloc[i:i + chunksize]
    elif ftype == "json":
        data = json.load(fh)
        if isinstance(data, dict):
            data = data.get("transactions") or data.get("data") or data.get("records")
        if not isinstance(data, list):
            raise IngestionError("JSON must be an array of transaction objects")
        df = pd.DataFrame(data).astype(str).replace({"None": "", "nan": ""})
        for i in range(0, len(df), chunksize):
            yield df.iloc[i:i + chunksize]


def read_preview(fh, ftype: str, n: int = 8) -> tuple[list[str], list[dict], int | None]:
    frames = iter_frames(fh, ftype, chunksize=max(n, 1000))
    first = next(frames, None)
    if first is None or first.empty:
        raise IngestionError("File contains no rows")
    return [str(c) for c in first.columns], first.head(n).to_dict(orient="records"), None


# ---------------------------------------------------------------- value parsing
_AMOUNT_JUNK = re.compile(r"[^\d.,\-()]")


def parse_amount(raw: str) -> Decimal | None:
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "n/a", "na", "null", "none", "-"):
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")")) or "-" in s[:3]
    s = _AMOUNT_JUNK.sub("", s).replace("(", "").replace(")", "").replace("-", "")
    if s.count(",") and s.count("."):
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif s.count(",") == 1 and len(s.split(",")[1]) == 2:
        s = s.replace(",", ".")  # European decimal comma
    else:
        s = s.replace(",", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse mixed timestamp strings to UTC. Detects day-first formats (e.g. 31/12/2025)."""
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s, errors="coerce", utc=True, format="ISO8601")
    rest = iso.isna() & (s != "")
    if rest.any():
        sub = s[rest]
        first = sub.str.extract(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")
        dayfirst = bool(first[0].dropna().astype(int).gt(12).any())
        parsed = pd.Series(pd.NaT, index=sub.index, dtype="datetime64[ns, UTC]")
        for fmt in (["%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M"] if dayfirst else
                    ["%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%m-%d-%Y %H:%M"]):
            m = parsed.isna()
            if not m.any():
                break
            parsed[m] = pd.to_datetime(sub[m], errors="coerce", utc=True, format=fmt)
        m = parsed.isna()
        if m.any():  # last resort: pandas inference
            parsed[m] = pd.to_datetime(sub[m], errors="coerce", utc=True, dayfirst=dayfirst, format="mixed")
        iso[rest] = parsed
    return iso


def norm_account(v: str) -> str:
    return re.sub(r"[\s\-]", "", str(v)).upper()


def clean_text(v) -> str | None:
    s = re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""
    return s or None


BUSINESS_TOKENS = re.compile(r"\b(llc|inc|ltd|limited|corp|corporation|co|company|group|plc|gmbh|sa|bv|holdings|trading|"
                             r"logistics|consulting|enterprises|services|partners|imports|exports|clinic|bank|store|supply)\b", re.I)


def classify_entity(name: str | None) -> str:
    if not name:
        return "unknown"
    return "business" if BUSINESS_TOKENS.search(name) else "individual"


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- staging
def staging_key(sha: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120] or "upload"
    return f"imports/{sha}/{safe}"


def stage_upload(db: Session, upload_fh, filename: str, max_bytes: int, user_id: int | None) -> ImportBatch:
    head = upload_fh.read(8192)
    ftype = detect_file_type(filename, head)
    upload_fh.seek(0)
    data = upload_fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise IngestionError(f"File exceeds the {max_bytes // (1024 * 1024)} MB import limit")
    if not data:
        raise IngestionError("File is empty")
    sha = hashlib.sha256(data).hexdigest()
    storage = get_storage()
    key = staging_key(sha, filename)
    if not storage.exists(key):
        storage.put(key, io.BytesIO(data))
    try:
        columns, sample, _ = read_preview(io.BytesIO(data), ftype)
    except IngestionError:
        raise
    except Exception as e:  # malformed file
        raise IngestionError(f"Could not parse file: {type(e).__name__}") from e
    mapping = suggest_mapping(columns)
    batch = ImportBatch(filename=Path(filename).name[:500], file_type=ftype, file_sha256=sha, file_size=len(data),
                        status="pending", column_mapping={k: v["column"] for k, v in mapping.items()},
                        summary={"columns": columns, "suggested_mapping": mapping,
                                 "sample": [{k: str(v)[:200] for k, v in row.items()} for row in sample],
                                 "storage_key": key},
                        created_by_id=user_id)
    db.add(batch)
    db.flush()
    previous = db.scalar(select(func.count()).select_from(ImportBatch).where(
        ImportBatch.file_sha256 == sha, ImportBatch.status == "completed", ImportBatch.id != batch.id))
    if previous:
        batch.summary = {**batch.summary, "previously_imported": True}
    return batch


def validate_mapping(mapping: dict[str, str], columns: list[str]) -> list[str]:
    errs = []
    for f in REQUIRED:
        if not mapping.get(f):
            errs.append(f"Required field '{CANONICAL_FIELDS[f]['label']}' is not mapped")
    for f, col in mapping.items():
        if f not in CANONICAL_FIELDS:
            errs.append(f"Unknown target field '{f}'")
        elif col and col not in columns:
            errs.append(f"Column '{col}' (for {f}) does not exist in the file")
    used = [c for c in mapping.values() if c]
    if len(used) != len(set(used)):
        errs.append("The same source column is mapped to more than one field")
    return errs


# ---------------------------------------------------------------- import
@dataclass
class _Ctx:
    accounts: dict[str, int]
    account_names: dict[str, str | None]
    seen_ids: set[str]
    seen_keys: dict[tuple, str]
    errors: list[dict]
    counts: dict[str, int]
    skip_content_duplicates: bool = True


MAX_ERRORS_STORED = 500


def _truthy(v: str) -> bool | None:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "t", "fraud", "suspicious"):
        return True
    if s in ("0", "false", "no", "n", "f", "legit", "normal"):
        return False
    return None


def _process_chunk(db: Session, df: pd.DataFrame, mapping: dict[str, str], batch: ImportBatch, ctx: _Ctx, row_offset: int):
    n = len(df)
    col = lambda f: df[mapping[f]].astype(str) if mapping.get(f) else pd.Series([""] * n, index=df.index)  # noqa: E731
    ts = parse_timestamps(col("timestamp"))
    rows, new_accounts = [], {}
    for i, idx in enumerate(df.index):
        rownum = row_offset + i + 2  # +1 header, +1 one-based
        problems, warnings = [], []
        snd, rcv = norm_account(col("sender_account")[idx]), norm_account(col("receiver_account")[idx])
        if not snd or snd in ("NAN", "NONE"):
            problems.append("missing sender account")
        if not rcv or rcv in ("NAN", "NONE"):
            problems.append("missing receiver account")
        if snd and snd == rcv:
            problems.append("sender and receiver accounts are identical")
        amount = parse_amount(col("amount")[idx])
        if amount is None:
            problems.append(f"unparseable amount '{str(col('amount')[idx])[:40]}'")
        elif amount <= 0:
            problems.append(f"non-positive amount {amount}")
        elif amount > Decimal("1e13"):
            problems.append("amount out of range")
        t = ts[idx]
        if pd.isna(t):
            problems.append(f"unparseable timestamp '{str(col('timestamp')[idx])[:40]}'")
        if problems:
            ctx.counts["invalid"] += 1
            if len(ctx.errors) < MAX_ERRORS_STORED:
                ctx.errors.append({"row": rownum, "errors": problems})
            continue
        t = t.to_pydatetime()
        txn_id = clean_text(col("txn_id")[idx])
        if not txn_id:
            raw = f"{snd}|{rcv}|{amount}|{t.isoformat()}|{col('reference')[idx]}"
            txn_id = "GEN-" + hashlib.sha1(raw.encode()).hexdigest()[:16].upper()
            warnings.append("transaction id missing - generated from content hash")
        txn_id = txn_id[:128]
        if txn_id in ctx.seen_ids:
            ctx.counts["duplicate"] += 1
            if len(ctx.errors) < MAX_ERRORS_STORED:
                ctx.errors.append({"row": rownum, "errors": [f"duplicate transaction id {txn_id} (skipped)"], "level": "duplicate"})
            continue
        ctx.seen_ids.add(txn_id)
        key = (snd, rcv, str(amount.quantize(Decimal('0.01'))), t.isoformat())
        if key in ctx.seen_keys:
            warnings.append(f"possible duplicate of {ctx.seen_keys[key]} (same accounts, amount and time)")
        else:
            ctx.seen_keys[key] = txn_id
        s_name, r_name = clean_text(col("sender_name")[idx]), clean_text(col("receiver_name")[idx])
        if mapping.get("sender_name") and not s_name:
            warnings.append("sender name missing")
        if mapping.get("receiver_name") and not r_name:
            warnings.append("receiver name missing")
        bank, rbank = clean_text(col("bank")[idx]), clean_text(col("receiver_bank")[idx])
        for acct, name, bk in ((snd, s_name, bank), (rcv, r_name, rbank)):
            if acct not in ctx.accounts and acct not in new_accounts:
                new_accounts[acct] = {"name": name, "bank": bk}
            elif acct in new_accounts and not new_accounts[acct]["name"] and name:
                new_accounts[acct]["name"] = name
        ip = clean_text(col("ip_address")[idx])
        status = (clean_text(col("status")[idx]) or "unknown").lower()[:32]
        gt_label = _truthy(col("ground_truth_label")[idx]) if mapping.get("ground_truth_label") else None
        gt_pattern = clean_text(col("ground_truth_pattern")[idx]) if mapping.get("ground_truth_pattern") else None
        if warnings:
            ctx.counts["warnings"] += 1
        rows.append({
            "txn_id": txn_id, "sender": snd, "receiver": rcv, "sender_name": s_name, "receiver_name": r_name,
            "amount": amount.quantize(Decimal("0.01")), "currency": (clean_text(col("currency")[idx]) or "USD").upper()[:8],
            "timestamp": t, "txn_type": (clean_text(col("txn_type")[idx]) or "unspecified").lower()[:64],
            "bank": bank, "reference": (clean_text(col("reference")[idx]) or None),
            "ip_address": ip[:64] if ip else None, "status": status, "import_batch_id": batch.id,
            "data_warnings": warnings, "ground_truth_label": gt_label,
            "ground_truth_pattern": (gt_pattern[:64] if gt_pattern else None),
        })
    # resolve accounts that already exist (needed for duplicate checks and inserts)
    if new_accounts:
        nums = list(new_accounts)
        for i in range(0, len(nums), 5000):
            for a in db.execute(select(Account.account_number, Account.id).where(Account.account_number.in_(nums[i:i + 5000]))):
                ctx.accounts[a.account_number] = a.id
    # existing ids in DB -> duplicates
    if rows:
        ids = [r["txn_id"] for r in rows]
        existing = set()
        for i in range(0, len(ids), 5000):
            existing |= set(db.scalars(select(Transaction.txn_id).where(Transaction.txn_id.in_(ids[i:i + 5000]))))
        if existing:
            ctx.counts["duplicate"] += len(existing)
            for r in rows:
                if r["txn_id"] in existing and len(ctx.errors) < MAX_ERRORS_STORED:
                    ctx.errors.append({"txn_id": r["txn_id"], "errors": ["transaction id already exists in database (skipped)"], "level": "duplicate"})
            rows = [r for r in rows if r["txn_id"] not in existing]
    # rows whose content (accounts, amount, time) matches a transaction already stored under another ID -
    # typical of overlapping bank exports. Skipped (counted as duplicates) unless the user opts out.
    if rows and ctx.skip_content_duplicates:
        known = [r for r in rows if r["sender"] in ctx.accounts and r["receiver"] in ctx.accounts]
        if known:
            # match on accounts + amount, timestamps within 60 s (exports often truncate seconds)
            senders = {ctx.accounts[r["sender"]] for r in known}
            lo = min(r["timestamp"] for r in known) - timedelta(seconds=60)
            hi = max(r["timestamp"] for r in known) + timedelta(seconds=60)
            existing_by_key: dict[tuple, list] = {}
            sender_list = list(senders)
            for i in range(0, len(sender_list), 2000):
                q = select(Transaction.txn_id, Transaction.sender_account_id, Transaction.receiver_account_id, Transaction.amount,
                           Transaction.timestamp).where(Transaction.sender_account_id.in_(sender_list[i:i + 2000]),
                                                        Transaction.timestamp >= lo, Transaction.timestamp <= hi)
                for t in db.execute(q):
                    ts = t.timestamp if t.timestamp.tzinfo else t.timestamp.replace(tzinfo=UTC)
                    key = (t.sender_account_id, t.receiver_account_id, Decimal(t.amount).quantize(Decimal("0.01")))
                    existing_by_key.setdefault(key, []).append((ts, t.txn_id))
            keep = []
            for r in rows:
                cands = existing_by_key.get((ctx.accounts.get(r["sender"]), ctx.accounts.get(r["receiver"]), r["amount"]), [])
                match = next((tid for ts, tid in cands if abs((ts - r["timestamp"]).total_seconds()) < 60), None)
                if match:
                    ctx.counts["duplicate"] += 1
                    if len(ctx.errors) < MAX_ERRORS_STORED:
                        ctx.errors.append({"txn_id": r["txn_id"], "errors": [f"same accounts and amount, within 60 s of existing transaction {match} (skipped)"], "level": "duplicate"})
                else:
                    keep.append(r)
            rows = keep
    # create accounts + one entity per new account (entities are never merged automatically)
    if new_accounts:
        for num, info in new_accounts.items():
            if num in ctx.accounts:
                continue
            name = info["name"]
            ent = Entity(entity_type=classify_entity(name), name=name or f"Unknown holder of {num}",
                         normalized_name=normalize_name(name), attributes={})
            db.add(ent)
            db.flush()
            acc = Account(account_number=num, bank=info["bank"], holder_name=name, entity_id=ent.id)
            db.add(acc)
            db.flush()
            ctx.accounts[num] = acc.id
    if rows:
        payload = []
        for r in rows:
            snd, rcv = r.pop("sender"), r.pop("receiver")
            r["sender_account_id"], r["receiver_account_id"] = ctx.accounts[snd], ctx.accounts[rcv]
            payload.append(r)
        db.execute(insert(Transaction), payload)
        ctx.counts["imported"] += len(payload)


def refresh_account_aggregates(db: Session) -> None:
    """Recompute per-account counts, totals and first/last activity from the transactions table."""
    out_q = select(Transaction.sender_account_id.label("aid"), func.count().label("n"), func.sum(Transaction.amount).label("s"),
                   func.min(Transaction.timestamp).label("f"), func.max(Transaction.timestamp).label("l")).group_by(Transaction.sender_account_id)
    in_q = select(Transaction.receiver_account_id.label("aid"), func.count().label("n"), func.sum(Transaction.amount).label("s"),
                  func.min(Transaction.timestamp).label("f"), func.max(Transaction.timestamp).label("l")).group_by(Transaction.receiver_account_id)
    agg: dict[int, dict] = {}
    for kind, q in (("out", out_q), ("in", in_q)):
        for r in db.execute(q):
            a = agg.setdefault(r.aid, {"n": 0, "in": Decimal(0), "out": Decimal(0), "f": None, "l": None})
            a["n"] += r.n
            a[kind] = r.s or Decimal(0)
            a["f"] = r.f if a["f"] is None or (r.f and r.f < a["f"]) else a["f"]
            a["l"] = r.l if a["l"] is None or (r.l and r.l > a["l"]) else a["l"]
    if agg:
        db.execute(update(Account), [{"id": k, "txn_count": v["n"], "total_in": v["in"], "total_out": v["out"],
                                      "first_seen": v["f"], "last_seen": v["l"]} for k, v in agg.items()])


def run_import(db: Session, batch: ImportBatch, mapping: dict[str, str], chunksize: int = 20000,
               skip_content_duplicates: bool = True) -> ImportBatch:
    storage = get_storage()
    key = batch.summary["storage_key"]
    batch.status, batch.column_mapping = "processing", mapping
    db.commit()
    ctx = _Ctx(accounts={}, account_names={}, seen_ids=set(), seen_keys={}, errors=[],
               counts={"imported": 0, "duplicate": 0, "invalid": 0, "warnings": 0},
               skip_content_duplicates=skip_content_duplicates)
    total = 0
    try:
        with storage.open(key) as fh:
            buf = io.BytesIO(fh.read())
        for df in iter_frames(buf, batch.file_type, chunksize=chunksize):
            _process_chunk(db, df, mapping, batch, ctx, total)
            total += len(df)
            batch.rows_total, batch.rows_imported = total, ctx.counts["imported"]
            db.commit()  # commit per chunk so progress is visible and memory stays bounded
        refresh_account_aggregates(db)
        batch.status = "completed"
    except Exception as e:
        db.rollback()
        batch = db.get(ImportBatch, batch.id)
        batch.status = "failed"
        ctx.errors.append({"row": None, "errors": [f"import aborted: {type(e).__name__}: {str(e)[:300]}"]})
    batch.rows_total = total
    batch.rows_imported = ctx.counts["imported"]
    batch.rows_duplicate = ctx.counts["duplicate"]
    batch.rows_invalid = ctx.counts["invalid"]
    batch.rows_with_warnings = ctx.counts["warnings"]
    batch.errors = ctx.errors
    batch.has_ground_truth = bool(mapping.get("ground_truth_label"))
    batch.completed_at = utcnow()
    db.commit()
    return batch
