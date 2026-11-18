"""Evidence vault: write-once storage, SHA-256 integrity, and a hash-chained chain-of-custody log.

Each CustodyEvent stores event_hash = sha256(prev_hash | evidence_id | action | actor | timestamp | details | file sha256).
Altering, deleting or reordering any event breaks the chain, which verify_chain() detects.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.storage import TooLarge, get_storage
from app.core.timeutil import as_utc
from app.models import CaseEvent, CustodyEvent, Evidence, User, utcnow

GENESIS = "0" * 64

# extension -> (file_type, allowed MIME, magic-byte check)
ALLOWED = {
    ".pdf": ("pdf", "application/pdf", lambda h: h.startswith(b"%PDF-")),
    ".png": ("image", "image/png", lambda h: h.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image", "image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image", "image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    ".gif": ("image", "image/gif", lambda h: h[:6] in (b"GIF87a", b"GIF89a")),
    ".webp": ("image", "image/webp", lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP"),
    ".csv": ("csv", "text/csv", None),
    ".txt": ("text", "text/plain", None),
    ".md": ("text", "text/markdown", None),
    ".json": ("json", "application/json", None),
    ".xlsx": ("spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", lambda h: h.startswith(b"PK\x03\x04")),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", lambda h: h.startswith(b"PK\x03\x04")),
}
CATEGORIES = ("transaction_records", "bank_statement", "document", "image", "screenshot", "investigation_note",
              "correspondence", "supporting_document", "report")


class EvidenceError(Exception):
    pass


def sniff(filename: str, head: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise EvidenceError(f"File type '{ext or 'none'}' is not allowed. Allowed: {', '.join(sorted(ALLOWED))}")
    ftype, mime, check = ALLOWED[ext]
    if check is not None and not check(head):
        raise EvidenceError(f"File content does not match its '{ext}' extension")
    if check is None:  # text formats: must be decodable text, no NUL bytes
        if b"\x00" in head:
            raise EvidenceError("Text evidence file contains binary data")
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(head) - 4:  # tolerate a multibyte char cut at the end of the sniff window
                raise EvidenceError("Text evidence file is not valid UTF-8") from e
    return ftype, mime


def _event_hash(prev: str, evidence_id: int, action: str, actor: str | None, ts: str, details: dict, sha: str) -> str:
    payload = json.dumps({"prev": prev, "evidence": evidence_id, "action": action, "actor": actor, "ts": ts,
                          "details": details, "file_sha256": sha}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def record_custody(db: Session, ev: Evidence, action: str, user: User | None, details: dict | None = None,
                   ip: str | None = None) -> CustodyEvent:
    last = db.scalar(select(CustodyEvent).where(CustodyEvent.evidence_id == ev.id).order_by(CustodyEvent.id.desc()).limit(1))
    prev = last.event_hash if last else GENESIS
    ts = utcnow().replace(microsecond=0)
    details = details or {}
    actor = user.email if user else None
    ce = CustodyEvent(evidence_id=ev.id, action=action, actor_id=user.id if user else None, actor_email=actor,
                      details=details, ip_address=ip, created_at=ts, prev_hash=prev,
                      event_hash=_event_hash(prev, ev.id, action, actor, ts.isoformat(), details, ev.sha256))
    db.add(ce)
    db.flush()
    return ce


def verify_chain(db: Session, ev: Evidence) -> dict:
    events = db.scalars(select(CustodyEvent).where(CustodyEvent.evidence_id == ev.id).order_by(CustodyEvent.id)).all()
    prev = GENESIS
    for i, e in enumerate(events):
        expected = _event_hash(prev, ev.id, e.action, e.actor_email, as_utc(e.created_at).isoformat(), e.details, ev.sha256)
        if e.prev_hash != prev or e.event_hash != expected:
            return {"valid": False, "events": len(events), "broken_at_event_id": e.id, "position": i}
        prev = e.event_hash
    return {"valid": True, "events": len(events), "head_hash": prev}


def next_code(db: Session) -> str:
    year = utcnow().year
    n = (db.scalar(select(func.count()).select_from(Evidence)) or 0) + 1
    while True:
        code = f"EV-{year}-{n:06d}"
        if not db.scalar(select(Evidence.id).where(Evidence.evidence_code == code)):
            return code
        n += 1


def add_evidence(db: Session, case_id: int, stream: BinaryIO, filename: str, description: str, category: str,
                 user: User, max_bytes: int, ip: str | None = None) -> Evidence:
    filename = Path(filename or "").name
    if not filename:
        raise EvidenceError("Filename is required")
    if category not in CATEGORIES:
        raise EvidenceError(f"Unknown category '{category}'")
    head = stream.read(8192)
    ftype, mime = sniff(filename, head)
    stream.seek(0)
    code = next_code(db)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:150]
    key = f"evidence/case-{case_id}/{code}/{safe}"
    try:
        sha, size = get_storage().put(key, stream, max_bytes=max_bytes)
    except TooLarge as e:
        raise EvidenceError(f"File exceeds the {max_bytes // (1024 * 1024)} MB evidence limit") from e
    if size == 0:
        raise EvidenceError("File is empty")
    ev = Evidence(evidence_code=code, case_id=case_id, original_filename=filename[:500], storage_key=key, content_type=mime,
                  file_type=ftype, category=category, size_bytes=size, sha256=sha, description=description or "",
                  uploaded_by_id=user.id)
    db.add(ev)
    db.flush()
    record_custody(db, ev, "uploaded", user, {"sha256": sha, "size": size, "filename": filename}, ip)
    db.add(CaseEvent(case_id=case_id, event_type="evidence_added", description=f"Evidence {code} added: {filename}",
                     actor_id=user.id, payload={"evidence_id": ev.id, "sha256": sha}))
    return ev


def verify_integrity(db: Session, ev: Evidence, user: User, ip: str | None = None) -> dict:
    storage = get_storage()
    if not storage.exists(ev.storage_key):
        actual, ok = None, False
    else:
        actual = storage.sha256(ev.storage_key)
        ok = actual == ev.sha256
    ev.verification_status = "verified" if ok else "failed"
    ev.last_verified_at = utcnow()
    record_custody(db, ev, "verified" if ok else "verification_failed", user,
                   {"expected_sha256": ev.sha256, "actual_sha256": actual, "match": ok}, ip)
    chain = verify_chain(db, ev)
    return {"evidence_id": ev.id, "hash_match": ok, "expected_sha256": ev.sha256, "actual_sha256": actual,
            "custody_chain": chain, "status": ev.verification_status}
