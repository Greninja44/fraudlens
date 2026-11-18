"""API tests: authentication, authorisation and the complete investigation workflow."""
import io
import json

import pytest

from tests.conftest import DATASETS, PASSWORD

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_login_and_me(client, auth):
    r = client.post("/api/auth/login", json={"email": "inv@test.example", "password": "wrong-password"})
    assert r.status_code == 401 and "password" not in r.text.lower().replace("invalid email or password", "")
    assert client.post("/api/auth/login", json={"email": "nobody@test.example", "password": PASSWORD}).status_code == 401
    me = client.get("/api/auth/me", headers=auth("inv")).json()
    assert me["role"] == "investigator" and "data:import" in me["permissions"] and "users:manage" not in me["permissions"]


def test_requires_token_and_rejects_bad_token(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_validation_errors_do_not_echo_input(client):
    r = client.post("/api/auth/login", json={"email": "not-an-email", "password": "SecretValue-999"})
    assert r.status_code == 422 and "SecretValue-999" not in r.text


def test_inactive_user_rejected(client, auth, users):
    h = auth("inv2")
    r = client.patch(f"/api/users/{users['inv2'].id}", json={"is_active": False}, headers=auth("admin"))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_role_permissions(client, auth, users):
    files = {"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}
    assert client.post("/api/imports/preview", files=files, headers=auth("rev")).status_code == 403
    assert client.post("/api/detection/runs", headers=auth("rev")).status_code == 403
    assert client.post("/api/users", json={"email": "n@test.example", "full_name": "N", "role": "admin", "password": "Abcdefghij1"},
                       headers=auth("inv")).status_code == 403
    assert client.put("/api/settings/detection", json={"flag_threshold": 40}, headers=auth("inv")).status_code == 403
    assert client.get("/api/audit", headers=auth("inv")).status_code == 403
    assert client.get("/api/audit", headers=auth("rev")).status_code == 200


def test_user_management_rules(client, auth, users):
    weak = client.post("/api/users", json={"email": "n@test.example", "full_name": "N", "role": "investigator", "password": "short"}, headers=auth("admin"))
    assert weak.status_code == 422
    ok = client.post("/api/users", json={"email": "N@Test.example", "full_name": "New", "role": "investigator", "password": "Abcdefghij1"}, headers=auth("admin"))
    assert ok.status_code == 201 and ok.json()["email"] == "n@test.example"
    dup = client.post("/api/users", json={"email": "n@test.example", "full_name": "N", "role": "investigator", "password": "Abcdefghij1"}, headers=auth("admin"))
    assert dup.status_code == 409
    assert client.patch(f"/api/users/{users['admin'].id}", json={"role": "reviewer"}, headers=auth("admin")).status_code == 400


def test_settings_validation(client, auth):
    assert client.put("/api/settings/detection", json={"nonsense": 1}, headers=auth("admin")).status_code == 422
    assert client.put("/api/settings/detection", json={"flag_threshold": -5}, headers=auth("admin")).status_code == 422
    r = client.put("/api/settings/detection", json={"flag_threshold": 45}, headers=auth("admin"))
    assert r.status_code == 200 and r.json()["values"]["flag_threshold"] == 45


def import_dataset(client, h, name="synthetic_transactions.csv"):
    with open(DATASETS / name, "rb") as fh:
        r = client.post("/api/imports/preview", files={"file": (name, fh, "text/csv")}, headers=h)
    assert r.status_code == 201, r.text
    prev = r.json()
    assert "amount" in prev["suggested_mapping"] and prev["sample"]
    mapping = {k: v["column"] for k, v in prev["suggested_mapping"].items()}
    r = client.post(f"/api/imports/{prev['id']}/commit", json={"mapping": mapping}, headers=h)
    assert r.status_code == 202, r.text
    b = client.get(f"/api/imports/{prev['id']}", headers=h).json()  # background task has run inside TestClient
    assert b["status"] == "completed", b
    return b


def test_full_investigation_workflow(client, auth, users):
    inv, rev = auth("inv"), auth("rev")
    # 1. import
    b = import_dataset(client, inv)
    assert b["rows_imported"] > 10000 and b["has_ground_truth"]
    assert client.post(f"/api/imports/{b['id']}/commit", json={"mapping": {}}, headers=inv).status_code == 409
    # 2. detection
    r = client.post("/api/detection/runs", headers=inv)
    assert r.status_code == 202
    run = client.get(f"/api/detection/runs/{r.json()['id']}", headers=inv).json()
    assert run["status"] == "completed" and run["evaluation"]["flagged"]["recall"] > 0.8
    dash = client.get("/api/dashboard", headers=inv).json()
    assert dash["totals"]["transactions"] == b["rows_imported"] and dash["totals"]["flagged"] == run["stats"]["flagged_transactions"]
    assert dash["activity"] and dash["by_pattern"]
    # 3. suspicious transactions / alerts
    flagged = client.get("/api/transactions?flagged=true&sort=risk_score&page_size=5", headers=inv).json()
    assert flagged["total"] == run["stats"]["flagged_transactions"] and flagged["items"][0]["risk_score"] >= 50
    circ = client.get("/api/alerts?pattern=circular&page_size=50", headers=inv).json()["items"]
    ring = json.loads((DATASETS / "dataset_card.json").read_text())["featured_ring"]
    victor = client.get(f"/api/accounts/lookup?number={ring['controller']}", headers=inv).json()
    ring_alert = next(a for a in circ if victor["id"] in a["details"]["account_ids"])
    # 4. open account
    acct = client.get(f"/api/accounts/{victor['id']}", headers=inv).json()
    assert acct["holder_name"] == "Victor Hale" and acct["risk_score"] >= 60 and acct["alerts"]
    # 5/6. network + follow the money
    g = client.get(f"/api/graph?account_id={victor['id']}&depth=2", headers=inv).json()
    assert any(n.get("is_focus") for n in g["nodes"]) and g["edges"]
    flow = client.get(f"/api/accounts/{victor['id']}/flow?depth=2", headers=inv).json()
    assert flow["downstream"] and flow["upstream"]
    shells = client.get("/api/accounts?search=Silverline", headers=inv).json()["items"]
    path = client.get(f"/api/graph/path?source={victor['id']}&target={shells[0]['id']}", headers=inv).json()
    assert path["found"]
    # 8. create case from the alert
    r = client.post("/api/cases", json={"title": "Operation Ledger - layering through shell companies", "summary": "Circular flows",
                                         "objectives": "Determine origin of funds", "priority": "high",
                                         "alert_ids": [ring_alert["id"]], "assignee_ids": [users["inv"].id]}, headers=inv)
    assert r.status_code == 201, r.text
    case = r.json()
    cid = case["id"]
    assert case["counts"]["accounts"] == len(set(ring_alert["details"]["account_ids"])) == 6  # whole ring: controller, alias, 4 shells
    roles = {a["account_number"]: a["case_role"] for a in case["accounts"]}
    assert roles[ring["controller"]] == "subject" and all(roles[s] == "related" for s in ring["shells"])
    assert case["counts"]["transactions"] == len(ring_alert["transaction_ids"])
    # 7. timeline for the case
    tl = client.get(f"/api/timeline?case_id={cid}", headers=inv).json()
    kinds = {e["kind"] for e in tl["events"]}
    assert {"transaction", "detection", "case"} <= kinds
    assert [e["timestamp"] for e in tl["events"]] == sorted(e["timestamp"] for e in tl["events"])
    # notes, findings
    assert client.post(f"/api/cases/{cid}/notes", json={"body": "Funds return to controller via alias account."}, headers=inv).status_code == 201
    r = client.post(f"/api/cases/{cid}/findings", json={"title": "Layering confirmed", "description": "Investigator conclusion",
                                                        "status": "confirmed", "related_alert_ids": [ring_alert["id"]]}, headers=inv)
    assert r.status_code == 201
    # 9. evidence
    r = client.post(f"/api/cases/{cid}/evidence", files={"file": ("statement.pdf", PDF, "application/pdf")},
                    data={"description": "Synthetic statement", "category": "bank_statement"}, headers=inv)
    assert r.status_code == 201, r.text
    evid = r.json()
    r = client.post(f"/api/cases/{cid}/evidence/transaction-extract", headers=inv)
    assert r.status_code == 201 and r.json()["category"] == "transaction_records"
    assert client.post(f"/api/cases/{cid}/evidence", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}, headers=inv).status_code == 422
    assert client.post(f"/api/cases/{cid}/evidence", files={"file": ("s.pdf", PDF, "application/pdf")}, headers=rev).status_code == 403
    dl = client.get(f"/api/evidence/{evid['id']}/download", headers=rev)
    assert dl.status_code == 200 and dl.content == PDF and dl.headers["x-evidence-sha256"] == evid["sha256"]
    v = client.post(f"/api/evidence/{evid['id']}/verify", headers=rev).json()
    assert v["hash_match"] and v["custody_chain"]["valid"]
    assert client.patch(f"/api/evidence/{evid['id']}", json={"description": "Updated"}, headers=inv).status_code == 200
    detail = client.get(f"/api/evidence/{evid['id']}", headers=inv).json()
    assert [c["action"] for c in detail["custody"]] == ["uploaded", "exported", "verified", "metadata_modified", "accessed"]
    assert detail["chain"]["valid"]
    # investigator not on the case cannot read its evidence or case
    assert client.get(f"/api/evidence/{evid['id']}/download", headers=auth("inv2")).status_code == 403
    assert client.get(f"/api/cases/{cid}", headers=auth("inv2")).status_code == 403
    # 10. report
    r = client.post(f"/api/cases/{cid}/reports", headers=inv)
    assert r.status_code == 201, r.text
    pdf = client.get(f"/api/reports/{r.json()['id']}/download", headers=inv)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 20000
    js = client.get(f"/api/cases/{cid}/export?format=json", headers=inv).json()
    assert js["investigator_findings"][0]["status"] == "confirmed" and js["automated_indicators"]
    assert all(i["kind"] in ("rule", "model") for i in js["automated_indicators"])
    csv = client.get(f"/api/cases/{cid}/export?format=csv", headers=inv)
    assert csv.text.splitlines()[0].startswith("txn_id,timestamp")
    tl_csv = client.get(f"/api/timeline/export?case_id={cid}&format=csv", headers=inv)
    assert tl_csv.status_code == 200 and "evidence" in tl_csv.text
    # status workflow: investigators can't close; reviewer can after review
    assert client.post(f"/api/cases/{cid}/status", json={"status": "under_investigation"}, headers=inv).status_code == 200
    assert client.post(f"/api/cases/{cid}/status", json={"status": "closed"}, headers=inv).status_code == 409
    assert client.post(f"/api/cases/{cid}/status", json={"status": "pending_review"}, headers=inv).status_code == 200
    assert client.post(f"/api/cases/{cid}/status", json={"status": "closed"}, headers=inv).status_code == 403
    r = client.post(f"/api/cases/{cid}/status", json={"status": "closed", "comment": "Referred"}, headers=rev)
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert client.post(f"/api/cases/{cid}/notes", json={"body": "late"}, headers=inv).status_code == 409
    # audit trail recorded
    actions = {a["action"] for a in client.get("/api/audit?page_size=200", headers=rev).json()["items"]}
    assert {"import.started", "detection.started", "case.created", "evidence.uploaded", "report.generated", "case.status_changed"} <= actions


def test_alert_review_and_entity_link_decisions(client, auth):
    inv = auth("inv")
    import_dataset(client, inv)
    client.post("/api/detection/runs", headers=inv)
    a = client.get("/api/alerts?page_size=1", headers=inv).json()["items"][0]
    r = client.post(f"/api/alerts/{a['id']}/review", json={"review_status": "dismissed", "note": "benign"}, headers=auth("rev"))
    assert r.status_code == 200 and r.json()["review_status"] == "dismissed"
    assert client.post(f"/api/alerts/{a['id']}/review", json={"review_status": "bogus"}, headers=inv).status_code == 422
    assert client.post("/api/entities/links/recompute", headers=inv).status_code == 200
    links = client.get("/api/entities/links?link_type=possible_duplicate", headers=inv).json()["items"]
    assert links
    l = links[0]
    assert client.post(f"/api/entities/links/{l['id']}/merge", json={"keep_entity_id": l["entity_a_id"]}, headers=inv).status_code == 409
    assert client.post(f"/api/entities/links/{l['id']}/decision", json={"status": "verified"}, headers=auth("rev")).status_code == 200
    assert client.post(f"/api/entities/links/{l['id']}/merge", json={"keep_entity_id": l["entity_a_id"]}, headers=auth("rev")).status_code == 403
    assert client.post(f"/api/entities/links/{l['id']}/merge", json={"keep_entity_id": l["entity_a_id"]}, headers=inv).status_code == 200


def test_transaction_filters_and_export(client, auth):
    inv = auth("inv")
    import_dataset(client, inv, "messy_bank_export.csv")
    r = client.get("/api/transactions?min_amount=5000&sort=amount&order=asc&page_size=10", headers=inv).json()
    amounts = [t["amount"] for t in r["items"]]
    assert all(a >= 5000 for a in amounts) and amounts == sorted(amounts)
    r = client.get("/api/transactions?date_from=2025-06-01T00:00:00Z&date_to=2025-06-30T23:59:59Z", headers=inv).json()
    assert all(t["timestamp"].startswith("2025-06") for t in r["items"])
    exp = client.get("/api/transactions/export?min_amount=5000", headers=inv)
    assert exp.status_code == 200 and len(exp.text.strip().splitlines()) == client.get("/api/transactions?min_amount=5000", headers=inv).json()["total"] + 1


def test_datasets_download_is_confined(client, auth):
    inv = auth("inv")
    assert client.get("/api/datasets", headers=inv).json()["files"]
    assert client.get("/api/datasets/dataset_card.json", headers=inv).status_code == 404  # only data files
    assert client.get("/api/datasets/..%2F..%2Fbackend%2Fpyproject.toml", headers=inv).status_code == 404
    assert client.get("/api/datasets/messy_bank_export.csv", headers=inv).status_code == 200
