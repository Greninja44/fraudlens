"""Management commands.

  python -m app.cli create-user EMAIL "Full Name" ROLE        (prompts for password)
  python -m app.cli seed-users                                  demo admin/investigator/reviewer accounts
  python -m app.cli load-demo [--file ../datasets/synthetic_transactions.csv]
                                                                import + detection + entity links
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password, validate_password_strength
from app.models import Role, User

DEMO_USERS = [("admin@fraudlens.local", "Alex Morgan", Role.ADMIN),
              ("investigator@fraudlens.local", "Priya Raman", Role.INVESTIGATOR),
              ("investigator2@fraudlens.local", "Daniel Osei", Role.INVESTIGATOR),
              ("reviewer@fraudlens.local", "Jordan Lee", Role.REVIEWER)]


def create_user(email, name, role, password):
    if role not in Role.ALL:
        sys.exit(f"role must be one of {Role.ALL}")
    if err := validate_password_strength(password):
        sys.exit(err)
    db = SessionLocal()
    if db.scalar(select(User).where(User.email == email.lower())):
        print(f"user {email} already exists")
        return
    db.add(User(email=email.lower(), full_name=name, role=role, hashed_password=hash_password(password)))
    db.commit()
    print(f"created {role} {email}")


def seed_users():
    s = get_settings()
    pw = os.environ.get("FL_DEMO_PASSWORD")
    if not pw:
        if s.env != "development":
            sys.exit("Set FL_DEMO_PASSWORD to seed demo users outside development")
        pw = "FraudLens-Demo-2026"
    for email, name, role in DEMO_USERS:
        create_user(email, name, role, pw)
    print("demo users ready (password from FL_DEMO_PASSWORD, or the documented development default)")


def load_demo(path: Path, if_empty: bool = False):
    from sqlalchemy import func
    from app.detection import engine
    from app.models import Transaction
    from app.services import entities, ingestion
    db = SessionLocal()
    if if_empty and db.scalar(select(func.count()).select_from(Transaction)):
        print("transactions already present - skipping demo data load")
        return
    admin = db.scalar(select(User).where(User.role == Role.ADMIN))
    with open(path, "rb") as fh:
        batch = ingestion.stage_upload(db, fh, path.name, get_settings().max_import_mb * 2**20, admin.id if admin else None)
    db.commit()
    batch = ingestion.run_import(db, batch, batch.column_mapping)
    print(f"import {batch.status}: {batch.rows_imported} imported, {batch.rows_duplicate} duplicates, {batch.rows_invalid} invalid")
    run = engine.start_run(db, admin.id if admin else None)
    run = engine.run_detection(db, run)
    print(f"detection run {run.id}: {run.stats.get('flagged_transactions')} flagged, {run.stats.get('alerts')} alerts")
    print("entity links:", entities.compute_links(db))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user"); cu.add_argument("email"); cu.add_argument("name"); cu.add_argument("role")
    sub.add_parser("seed-users")
    sub.add_parser("detect")
    ld = sub.add_parser("load-demo"); ld.add_argument("--file", type=Path, default=get_settings().datasets_dir / "synthetic_transactions.csv")
    ld.add_argument("--if-empty", action="store_true", help="only load when the database has no transactions")
    a = ap.parse_args()
    if a.cmd == "create-user":
        pw = getpass.getpass("Password: ")
        create_user(a.email, a.name, a.role, pw)
    elif a.cmd == "seed-users":
        seed_users()
    elif a.cmd == "detect":
        from app.detection import engine
        db = SessionLocal()
        run = engine.run_detection(db, engine.start_run(db, None))
        print(f"detection run {run.id}: {run.stats.get('flagged_transactions')} flagged, {run.stats.get('alerts')} alerts")
        print("evaluation:", (run.evaluation or {}).get("flagged"))
    elif a.cmd == "load-demo":
        load_demo(a.file, a.if_empty)


if __name__ == "__main__":
    main()
