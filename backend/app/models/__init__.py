"""SQLAlchemy ORM models.

Terminology used throughout the data model (and the UI):
  * Alert            - an automated *indicator* of suspicious activity (rule hit or model prediction). Not proof.
  * anomaly_score    - an unsupervised model output (Isolation Forest). Not proof.
  * CaseFinding      - an investigator-recorded conclusion. Only these represent confirmed findings.
  * ground_truth_*   - labels shipped with *synthetic* datasets, used only to evaluate detection quality;
                       never read by the detection engine.
"""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JSONType


def utcnow() -> datetime:
    return datetime.now(UTC)


TS = DateTime(timezone=True)


class Role:
    ADMIN = "admin"
    INVESTIGATOR = "investigator"
    REVIEWER = "reviewer"
    ALL = (ADMIN, INVESTIGATOR, REVIEWER)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=Role.INVESTIGATOR)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(16))
    file_sha256: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|processing|completed|failed
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_invalid: Mapped[int] = mapped_column(Integer, default=0)
    rows_with_warnings: Mapped[int] = mapped_column(Integer, default=0)
    column_mapping: Mapped[dict] = mapped_column(JSONType, default=dict)
    errors: Mapped[list] = mapped_column(JSONType, default=list)  # capped sample of row-level errors
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    has_ground_truth: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[User | None] = relationship()


class Entity(Base):
    """A real-world party (individual or business) that holds one or more accounts."""
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16), default="individual")  # individual|business|unknown
    name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)  # emails, phones, ip addresses seen
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    accounts: Mapped[list["Account"]] = relationship(back_populates="entity", foreign_keys="Account.entity_id")


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    bank: Mapped[str | None] = mapped_column(String(128))
    holder_name: Mapped[str | None] = mapped_column(String(255))
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), index=True)
    first_seen: Mapped[datetime | None] = mapped_column(TS)
    last_seen: Mapped[datetime | None] = mapped_column(TS)
    txn_count: Mapped[int] = mapped_column(Integer, default=0)
    total_in: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_out: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    patterns: Mapped[list] = mapped_column(JSONType, default=list)
    entity: Mapped[Entity | None] = relationship(back_populates="accounts", foreign_keys=[entity_id])


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    txn_id: Mapped[str] = mapped_column(String(128), unique=True)
    sender_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    receiver_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    receiver_name: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    timestamp: Mapped[datetime] = mapped_column(TS, index=True)
    txn_type: Mapped[str | None] = mapped_column(String(64))
    bank: Mapped[str | None] = mapped_column(String(128))
    reference: Mapped[str | None] = mapped_column(String(255), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str | None] = mapped_column(String(32))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), index=True)
    data_warnings: Mapped[list] = mapped_column(JSONType, default=list)
    # Detection outputs (overwritten by each detection run)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    patterns: Mapped[list] = mapped_column(JSONType, default=list)
    # Synthetic-data evaluation labels (never used by detectors)
    ground_truth_label: Mapped[bool | None] = mapped_column(Boolean)
    ground_truth_pattern: Mapped[str | None] = mapped_column(String(64))

    sender: Mapped[Account] = relationship(foreign_keys=[sender_account_id])
    receiver: Mapped[Account] = relationship(foreign_keys=[receiver_account_id])

    __table_args__ = (Index("ix_txn_sender_ts", "sender_account_id", "timestamp"),
                      Index("ix_txn_receiver_ts", "receiver_account_id", "timestamp"))


class DetectionRun(Base):
    __tablename__ = "detection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|completed|failed
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    evaluation: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class Alert(Base):
    """An automated indicator. kind='rule' (explainable rule) or kind='model' (anomaly model prediction)."""
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("detection_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="rule")
    pattern: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))  # low|medium|high|critical
    confidence: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(255))
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    transaction_ids: Mapped[list] = mapped_column(JSONType, default=list)  # internal transaction ids
    first_txn_at: Mapped[datetime | None] = mapped_column(TS)
    last_txn_at: Mapped[datetime | None] = mapped_column(TS)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    review_status: Mapped[str] = mapped_column(String(16), default="new")  # new|acknowledged|dismissed|escalated
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    account: Mapped[Account | None] = relationship()


class EntityLink(Base):
    """A *suggested* relationship between two entities. Only investigators can verify; nothing auto-merges."""
    __tablename__ = "entity_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_a_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    entity_b_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    link_type: Mapped[str] = mapped_column(String(32))  # possible_duplicate|related
    confidence: Mapped[float] = mapped_column(Float)
    signals: Mapped[list] = mapped_column(JSONType, default=list)  # [{signal, weight, detail}]
    status: Mapped[str] = mapped_column(String(16), default="suggested")  # suggested|verified|rejected
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(TS)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    entity_a: Mapped[Entity] = relationship(foreign_keys=[entity_a_id])
    entity_b: Mapped[Entity] = relationship(foreign_keys=[entity_b_id])
    __table_args__ = (UniqueConstraint("entity_a_id", "entity_b_id", name="uq_entity_link_pair"),)


class CaseStatus:
    OPEN = "open"
    UNDER_INVESTIGATION = "under_investigation"
    PENDING_REVIEW = "pending_review"
    CLOSED = "closed"
    ESCALATED = "escalated"
    ALL = (OPEN, UNDER_INVESTIGATION, PENDING_REVIEW, CLOSED, ESCALATED)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    objectives: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.OPEN, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    assignees: Mapped[list[User]] = relationship(secondary="case_assignees")


class CaseAssignee(Base):
    __tablename__ = "case_assignees"
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class CaseAccount(Base):
    __tablename__ = "case_accounts"
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), default="subject")  # subject|counterparty|witness
    note: Mapped[str | None] = mapped_column(Text)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    account: Mapped[Account] = relationship()


class CaseTransaction(Base):
    __tablename__ = "case_transactions"
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), primary_key=True)
    note: Mapped[str | None] = mapped_column(Text)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    transaction: Mapped[Transaction] = relationship()


class CaseNote(Base):
    __tablename__ = "case_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    author: Mapped[User | None] = relationship()


class CaseFinding(Base):
    """Investigator conclusion - the only place a 'confirmed' finding can be recorded."""
    __tablename__ = "case_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|confirmed|refuted
    related_alert_ids: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    author: Mapped[User | None] = relationship()


class CaseEvent(Base):
    """Activity log of a case (status changes, assignments, additions) - feeds the investigation timeline."""
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    actor: Mapped[User | None] = relationship()


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    file_type: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(48), default="supporting_document")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")  # unverified|verified|failed
    last_verified_at: Mapped[datetime | None] = mapped_column(TS)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    uploaded_by: Mapped[User | None] = relationship()
    case: Mapped[Case] = relationship()


class CustodyEvent(Base):
    """Append-only, hash-chained chain-of-custody record for an evidence item."""
    __tablename__ = "custody_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))  # uploaded|accessed|downloaded|exported|verified|verification_failed|metadata_modified
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    format: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(512))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    generated_by: Mapped[User | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(48))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
