import io
import os
import stat

import pytest
from sqlalchemy import select

from app.core.storage import ObjectExists, TooLarge
from app.models import Case, CustodyEvent
from app.services import evidence as ev

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def case(db, users):
    c = Case(case_number="FC-T-1", title="Test case", created_by_id=users["inv"].id)
    db.add(c)
    db.commit()
    return c


def test_upload_hash_readonly_and_custody(db, users, case, storage):
    e = ev.add_evidence(db, case.id, io.BytesIO(PDF), "statement.pdf", "bank statement", "bank_statement", users["inv"], 10**6)
    db.commit()
    import hashlib
    assert e.sha256 == hashlib.sha256(PDF).hexdigest() and e.size_bytes == len(PDF)
    path = storage._path(e.storage_key)
    assert not os.stat(path).st_mode & stat.S_IWUSR  # original stored read-only
    events = db.scalars(select(CustodyEvent).where(CustodyEvent.evidence_id == e.id)).all()
    assert [x.action for x in events] == ["uploaded"] and events[0].prev_hash == ev.GENESIS
    assert ev.verify_chain(db, e)["valid"]


def test_storage_never_overwrites(storage):
    storage.put("x/a.txt", io.BytesIO(b"one"))
    with pytest.raises(ObjectExists):
        storage.put("x/a.txt", io.BytesIO(b"two"))
    assert storage.open("x/a.txt").read() == b"one"
    with pytest.raises(TooLarge):
        storage.put("x/b.txt", io.BytesIO(b"x" * 100), max_bytes=10)
    assert not storage.exists("x/b.txt")
    storage.put("../../etc/escape", io.BytesIO(b"x"))  # traversal segments are stripped: stays inside root
    assert (storage.root / "etc" / "escape").exists()


@pytest.mark.parametrize("name,data", [("fake.pdf", b"MZ\x90\x00binary"), ("img.png", b"GIF89a....."), ("run.exe", b"MZ"),
                                       ("notes.txt", b"abc\x00def"), ("noext", b"hello")])
def test_rejects_disallowed_or_spoofed(db, users, case, name, data):
    with pytest.raises(ev.EvidenceError):
        ev.add_evidence(db, case.id, io.BytesIO(data), name, "", "document", users["inv"], 10**6)


def test_size_limit(db, users, case):
    with pytest.raises(ev.EvidenceError):
        ev.add_evidence(db, case.id, io.BytesIO(PNG + b"\x00" * 2000), "big.png", "", "image", users["inv"], 1000)


def test_tampered_file_detected(db, users, case, storage):
    e = ev.add_evidence(db, case.id, io.BytesIO(PNG), "shot.png", "", "screenshot", users["inv"], 10**6)
    db.commit()
    assert ev.verify_integrity(db, e, users["rev"])["hash_match"] is True
    path = storage._path(e.storage_key)
    os.chmod(path, 0o600)
    with open(path, "ab") as f:
        f.write(b"tamper")
    res = ev.verify_integrity(db, e, users["rev"])
    db.commit()
    assert res["hash_match"] is False and e.verification_status == "failed"
    actions = [x.action for x in db.scalars(select(CustodyEvent).where(CustodyEvent.evidence_id == e.id).order_by(CustodyEvent.id))]
    assert actions == ["uploaded", "verified", "verification_failed"]


def test_tampered_custody_log_detected(db, users, case):
    e = ev.add_evidence(db, case.id, io.BytesIO(PDF), "a.pdf", "", "document", users["inv"], 10**6)
    ev.record_custody(db, e, "accessed", users["rev"])
    ev.record_custody(db, e, "exported", users["inv"])
    db.commit()
    assert ev.verify_chain(db, e) == {"valid": True, "events": 3, "head_hash": ev.verify_chain(db, e)["head_hash"]}
    second = db.scalars(select(CustodyEvent).where(CustodyEvent.evidence_id == e.id).order_by(CustodyEvent.id)).all()[1]
    second.actor_email = "someone-else@test.example"
    db.commit()
    res = ev.verify_chain(db, e)
    assert res["valid"] is False and res["broken_at_event_id"] == second.id
    # deleting an event also breaks the chain
    second.actor_email = "rev@test.example"
    db.commit()
    assert ev.verify_chain(db, e)["valid"]
    db.delete(second)
    db.commit()
    assert ev.verify_chain(db, e)["valid"] is False
