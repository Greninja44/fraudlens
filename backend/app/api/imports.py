import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404, page_params, row
from app.core import db as dbmod
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, require
from app.models import ImportBatch, User
from app.services import ingestion
from app.services.audit import audit

router = APIRouter(tags=["ingestion"])
BATCH_FIELDS = ["id", "filename", "file_type", "file_sha256", "file_size", "status", "rows_total", "rows_imported",
                "rows_duplicate", "rows_invalid", "rows_with_warnings", "column_mapping", "has_ground_truth", "created_at",
                "completed_at", "created_by_id"]


def batch_out(b: ImportBatch, full: bool = False) -> dict:
    d = row(b, BATCH_FIELDS)
    d["created_by"] = b.created_by.full_name if b.created_by else None
    s = b.summary or {}
    d["previously_imported"] = bool(s.get("previously_imported"))
    if full:
        d.update({"columns": s.get("columns", []), "suggested_mapping": s.get("suggested_mapping", {}), "sample": s.get("sample", []),
                  "errors": b.errors or [], "fields": {k: {"label": v["label"], "required": v["required"]}
                                                       for k, v in ingestion.CANONICAL_FIELDS.items()}})
    return d


@router.post("/imports/preview", status_code=201)
async def preview_import(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                         user: User = Depends(require("data:import"))):
    s = get_settings()
    try:
        batch = ingestion.stage_upload(db, file.file, file.filename or "upload", s.max_import_mb * 1024 * 1024, user.id)
    except ingestion.IngestionError as e:
        raise HTTPException(422, str(e))
    audit(db, user, "import.staged", "import", batch.id, {"filename": batch.filename, "sha256": batch.file_sha256}, client_ip(request))
    db.commit()
    return batch_out(batch, full=True)


class CommitIn(BaseModel):
    mapping: dict[str, str | None]
    skip_content_duplicates: bool = True


def _run_import_bg(batch_id: int, mapping: dict, skip_content_duplicates: bool = True):
    db = dbmod.SessionLocal()
    try:
        b = db.get(ImportBatch, batch_id)
        ingestion.run_import(db, b, mapping, skip_content_duplicates=skip_content_duplicates)
    finally:
        db.close()


@router.post("/imports/{batch_id}/commit", status_code=202)
def commit_import(batch_id: int, body: CommitIn, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(require("data:import"))):
    b = get_or_404(db, ImportBatch, batch_id, "Import")
    if b.status != "pending":
        raise HTTPException(409, f"Import is already {b.status}")
    mapping = {k: v for k, v in body.mapping.items() if v}
    errs = ingestion.validate_mapping(mapping, b.summary.get("columns", []))
    if errs:
        raise HTTPException(422, "; ".join(errs))
    b.status, b.column_mapping = "queued", mapping
    audit(db, user, "import.started", "import", b.id, {"mapping": mapping, "skip_content_duplicates": body.skip_content_duplicates}, client_ip(request))
    db.commit()
    b.status = "pending"  # run_import sets processing
    bg.add_task(_run_import_bg, b.id, mapping, body.skip_content_duplicates)
    return batch_out(b)


@router.get("/imports")
def list_imports(page: int = 1, page_size: int = 25, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    total = db.scalar(select(func.count()).select_from(ImportBatch))
    items = db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).offset(off).limit(lim)).all()
    return {"total": total, "items": [batch_out(b) for b in items]}


@router.get("/imports/{batch_id}")
def get_import(batch_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return batch_out(get_or_404(db, ImportBatch, batch_id, "Import"), full=True)


# ---- sample datasets
@router.get("/datasets")
def list_datasets(_: User = Depends(get_current_user)):
    d = get_settings().datasets_dir
    card = {}
    if (d / "dataset_card.json").exists():
        card = json.loads((d / "dataset_card.json").read_text())
    files = []
    for name in card.get("files", []):
        p = d / name
        if p.exists():
            files.append({"name": name, "size_bytes": p.stat().st_size,
                          "description": "Messy bank export for testing column mapping and validation" if name.startswith("messy")
                          else "Synthetic transactions with ground-truth labels" + (" (Sep-Dec 2025 subset)" if "q4" in name else "")})
    return {"files": files, "card": card}


@router.get("/datasets/{name}")
def download_dataset(name: str, _: User = Depends(get_current_user)):
    d = get_settings().datasets_dir.resolve()
    card = d / "dataset_card.json"
    allowed = set(json.loads(card.read_text()).get("files", [])) if card.exists() else set()
    p = (d / Path(name).name).resolve()
    if name not in allowed or p.parent != d or not p.exists():
        raise HTTPException(404, "Dataset not found")
    return FileResponse(p, filename=p.name)
