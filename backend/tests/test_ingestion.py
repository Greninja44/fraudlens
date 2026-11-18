import io
import json
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models import Account, Entity, Transaction
from app.services import ingestion
from tests.conftest import DATASETS


def stage_and_run(db, data: bytes, name: str, mapping=None):
    b = ingestion.stage_upload(db, io.BytesIO(data), name, 50 * 2**20, None)
    db.commit()
    return ingestion.run_import(db, b, mapping or b.column_mapping)


def test_suggest_mapping_for_messy_headers():
    cols = ["Txn Ref No", "Value Date", "From A/C", "Remitter", "To A/C", "Beneficiary", "Amt", "Ccy", "Channel",
            "Narration", "Bank Name", "Client IP", "Txn Status"]
    m = {k: v["column"] for k, v in ingestion.suggest_mapping(cols).items()}
    assert m["txn_id"] == "Txn Ref No" and m["timestamp"] == "Value Date"
    assert m["sender_account"] == "From A/C" and m["receiver_account"] == "To A/C"
    assert m["amount"] == "Amt" and m["sender_name"] == "Remitter" and m["receiver_name"] == "Beneficiary"
    assert m["ip_address"] == "Client IP" and m["status"] == "Txn Status" and m["reference"] == "Narration"


def test_fuzzy_mapping_and_no_double_use():
    m = ingestion.suggest_mapping(["Transaction Amnt", "Sender Acount", "Receiver Acount", "Timestmp"])
    assert m["amount"]["column"] == "Transaction Amnt"
    assert m["sender_account"]["column"] == "Sender Acount" and m["sender_account"]["method"] == "fuzzy"
    used = [v["column"] for v in m.values()]
    assert len(used) == len(set(used))


@pytest.mark.parametrize("raw,expected", [
    ("$1,234.50", Decimal("1234.50")), ("1234", Decimal("1234")), ("(450.00)", Decimal("-450.00")), ("-$450.00", Decimal("-450.00")),
    ("1.234,56", Decimal("1234.56")), ("12,50", Decimal("12.50")), ("n/a", None), ("", None), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert ingestion.parse_amount(raw) == expected


def test_parse_timestamps_detects_day_first_and_iso():
    s = ingestion.parse_timestamps(pd.Series(["31/12/2025 04:41", "02/03/2025 10:00", "2025-05-01T10:00:00Z", "31/02/2025 10:00", "yesterday"]))
    assert s[0].month == 12 and s[0].day == 31
    assert s[1].month == 3 and s[1].day == 2  # day-first inferred from the file
    assert s[2].year == 2025 and s[2].hour == 10
    assert pd.isna(s[3]) and pd.isna(s[4])


def test_rejects_unsupported_and_spoofed_files(db):
    with pytest.raises(ingestion.IngestionError):
        ingestion.stage_upload(db, io.BytesIO(b"hello"), "data.exe", 1000, None)
    with pytest.raises(ingestion.IngestionError):
        ingestion.stage_upload(db, io.BytesIO(b"not a zip"), "data.xlsx", 1000, None)
    with pytest.raises(ingestion.IngestionError):
        ingestion.stage_upload(db, io.BytesIO(b"a,b\n1,2\n" * 100), "big.csv", 50, None)
    with pytest.raises(ingestion.IngestionError):
        ingestion.stage_upload(db, io.BytesIO(b'"just a string"'), "x.json", 1000, None)


def test_validate_mapping_reports_missing_required():
    errs = ingestion.validate_mapping({"amount": "Amt"}, ["Amt"])
    assert any("Sender account" in e for e in errs) and any("Timestamp" in e for e in errs)
    assert ingestion.validate_mapping({"amount": "Nope", "sender_account": "a", "receiver_account": "b", "timestamp": "t"}, ["a", "b", "t"])


def test_messy_import_validation_duplicates_and_missing_values(db):
    data = (DATASETS / "messy_bank_export.csv").read_bytes()
    b = stage_and_run(db, data, "messy_bank_export.csv")
    assert b.status == "completed"
    assert b.rows_total == 615
    assert b.rows_invalid == 6            # negative, unparseable amount, Feb 31, 'yesterday', missing sender, self-transfer
    assert b.rows_duplicate == 15         # 15 duplicated rows inside the file
    assert b.rows_imported == 615 - 6 - 15
    assert b.rows_with_warnings >= 25     # missing beneficiary names
    reasons = " ".join(" ".join(e["errors"]) for e in b.errors)
    for frag in ("non-positive amount", "unparseable amount", "unparseable timestamp", "missing sender", "identical"):
        assert frag in reasons
    t = db.scalar(select(Transaction).limit(1))
    assert t.amount > 0 and t.timestamp is not None and t.status == t.status.lower()


def test_reimport_marks_all_duplicates_and_no_double_counting(db):
    data = (DATASETS / "messy_bank_export.csv").read_bytes()
    first = stage_and_run(db, data, "messy_bank_export.csv")
    n = db.scalar(select(func.count()).select_from(Transaction))
    second = stage_and_run(db, data, "messy_bank_export.csv")
    assert second.rows_imported == 0
    assert second.rows_duplicate == first.rows_imported + 15
    assert db.scalar(select(func.count()).select_from(Transaction)) == n
    assert second.summary.get("previously_imported") is True


def test_json_and_xlsx_import(db):
    rows = [{"id": "J1", "from": "A-1", "to": "B-2", "amount": "100.5", "date": "2025-01-02 10:00", "sender_name": "Ann Lee", "receiver_name": "Bolt Trading LLC"},
            {"id": "J2", "from": "A-1", "to": "C-3", "amount": "200", "date": "2025-01-03 11:00"}]
    b = stage_and_run(db, json.dumps({"transactions": rows}).encode(), "t.json",
                      {"txn_id": "id", "sender_account": "from", "receiver_account": "to", "amount": "amount", "timestamp": "date",
                       "sender_name": "sender_name", "receiver_name": "receiver_name"})
    assert b.rows_imported == 2
    acct = db.scalar(select(Account).where(Account.account_number == "A1"))  # normalised: dashes removed
    assert acct and acct.txn_count == 2 and float(acct.total_out) == 300.5
    ent = db.scalar(select(Entity).join(Account, Account.entity_id == Entity.id).where(Account.account_number == "B2"))
    assert ent.entity_type == "business"
    buf = io.BytesIO()
    pd.DataFrame([{"Transaction ID": "X1", "Sender Account": "Z1", "Receiver Account": "Z2", "Amount": 50, "Timestamp": "2025-02-01 09:00"}]).to_excel(buf, index=False)
    b2 = stage_and_run(db, buf.getvalue(), "t.xlsx")
    assert b2.status == "completed" and b2.rows_imported == 1


def test_missing_transaction_id_generates_stable_id(db):
    csv = b"sender_account,receiver_account,amount,timestamp\nA1,B1,10,2025-01-01T00:00:00Z\n"
    b = stage_and_run(db, csv, "noid.csv")
    t = db.scalar(select(Transaction))
    assert b.rows_imported == 1 and t.txn_id.startswith("GEN-") and any("generated" in w for w in t.data_warnings)


def test_original_import_file_preserved(db, storage):
    data = (DATASETS / "messy_bank_export.csv").read_bytes()
    b = stage_and_run(db, data, "messy_bank_export.csv")
    assert storage.sha256(b.summary["storage_key"]) == b.file_sha256


def test_overlapping_export_with_different_ids_is_skipped(db):
    """The messy export re-uses synthetic transactions under new IDs: content matches are skipped by default."""
    base = stage_and_run(db, (DATASETS / "synthetic_transactions.csv").read_bytes(), "synthetic_transactions.csv")
    n = db.scalar(select(func.count()).select_from(Transaction))
    messy = stage_and_run(db, (DATASETS / "messy_bank_export.csv").read_bytes(), "messy_bank_export.csv")
    assert messy.rows_imported == 0 and messy.rows_invalid == 6
    assert db.scalar(select(func.count()).select_from(Transaction)) == n == base.rows_imported
    assert any("existing transaction" in " ".join(e["errors"]) for e in messy.errors)


def test_content_duplicate_skip_can_be_disabled(db):
    csv1 = b"transaction_id,sender_account,receiver_account,amount,timestamp\nA,S1,R1,10,2025-01-01T00:00:00Z\n"
    csv2 = b"transaction_id,sender_account,receiver_account,amount,timestamp\nB,S1,R1,10,2025-01-01T00:00:00Z\n"
    stage_and_run(db, csv1, "a.csv")
    b = ingestion.stage_upload(db, io.BytesIO(csv2), "b.csv", 10**6, None)
    db.commit()
    b = ingestion.run_import(db, b, b.column_mapping, skip_content_duplicates=False)
    assert b.rows_imported == 1
