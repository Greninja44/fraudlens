# Architecture

```
 Browser ── React SPA (Vite build, served by nginx) ──/api──▶ FastAPI (uvicorn)
                                                                │
          ┌──────────────────────┬──────────────────────┬───────┴─────────┬─────────────────────┐
          ▼                      ▼                      ▼                 ▼                     ▼
   services/ingestion     detection/engine       services/graph   services/evidence     services/reports
   (pandas, rapidfuzz)    rules.py + ml.py       (NetworkX)       (hash-chained         (ReportLab +
                          (pandas, sklearn)                        custody)              matplotlib)
          │                      │                      │                 │                     │
          └──────────────┬───────┴──────────────────────┘                 ▼                     │
                         ▼                                     core/storage (write-once) ◀──────┘
                PostgreSQL 16 (JSONB)                          local dir │ S3-compatible
```

## Backend

- **FastAPI with SQLAlchemy 2 and Pydantic v2.** Routes are in `app/api/*` and business logic is in `app/services/*` and `app/detection/*`. All SQL goes through the ORM or Core expressions with bound parameters; there is no string-built SQL.
- **Migrations:** Alembic (`backend/alembic`). JSON columns are `JSONB` on PostgreSQL and fall back to `JSON` elsewhere, so the suite also runs on SQLite.
- **Background work:** imports and detection runs are started with FastAPI `BackgroundTasks` and polled by the UI (`ImportBatch.status`, `DetectionRun.status`). Only one detection run may be in progress, and a run stuck for more than 30 minutes is marked failed. For multi-node deployments, swap in a job queue such as RQ or Celery; the service functions already take a session and an ID.

## Ingestion (Module A)

1. **Stage:** the upload is checked for type (extension plus content sniffing: zip magic for XLSX, no NUL bytes for CSV/JSON) and size, hashed, and stored unchanged at `imports/<sha256>/<name>`. A preview and suggested mapping are returned.
2. **Mapping:** headers are normalised and matched exactly against synonym lists. Remaining fields are fuzzy-matched (rapidfuzz ratio ≥ 85) using a **global best-first assignment**, so one field cannot steal a column that matches another field better. The user can override any mapping.
3. **Run:** CSV is streamed in 20k-row chunks, and each chunk is committed so progress is visible and memory stays bounded. Per row, the importer:
   - parses amounts (`$1,234.50`, `(450)`, `1.234,56`) and timestamps (ISO; day-first is inferred when any first component exceeds 12);
   - validates required fields, positive amounts, and distinct sender and receiver;
   - normalises account numbers, text and status;
   - generates content-hash IDs when IDs are missing.

   Exact duplicates (same ID in the file or already in the database) are skipped and counted. Rows that match an **already stored** transaction on accounts and amount, with timestamps within 60 s, under a different ID are treated as overlapping exports. They are skipped as duplicates by default (`skip_content_duplicates`, a checkbox in the mapping dialog). The same situation *within one file* only produces a "possible duplicate" warning, because two identical payments in the same minute can be legitimate. Accounts are created on first sight, and **each new account gets its own Entity**.
4. Account aggregates (totals and first/last seen) are recomputed, and the report stores counts plus up to 500 row-level issues.

## Detection (Module B)

`engine.run_detection` loads transactions in deterministic `id` order and excludes failed or declined ones. It runs the rules in `rules.py`, then the Isolation Forest in `ml.py`.

| Rule | Logic (defaults, all tunable) |
|---|---|
| Structuring | ≥ 3 transfers by one sender within 15% below the $10k threshold in 7 days, totalling > $10k |
| Smurfing | ≥ 3 distinct senders making near-threshold deposits to one receiver within 7 days |
| Rapid movement | inbound ≥ $2k followed within 48 h by ≤ 3 outbound transfers totalling 70–110% of it |
| Circular flow | **temporal** DFS from every edge ≥ $2k: each hop later than the previous one, within 14 days and within ±35% of the first amount, returning to the origin in ≤ 6 hops; deduplicated |
| Unusual behaviour | robust z-score (median/MAD of log amount) ≥ 4 against the sender's own history (≥ 8 txns); off-hours transfers for accounts that almost never transact at night |
| High velocity | ≥ 8 outgoing transactions in any 60-minute window (vectorised rolling count); contiguous hot windows merged into bursts |
| Account network | one IP used by ≥ 3 differently named holders; or two accounts sharing ≥ 3 *uncommon* counterparties with Jaccard ≥ 0.6 (counterparties with > 15 distinct partners, such as employers and merchants, are excluded) |
| Dormant reactivation | first activity of ≥ $5k after ≥ 180 days of inactivity, plus large activity in the following 14 days |

Every finding has a severity (low, medium, high or critical), a confidence in [0, 1], human-readable reasons, the involved accounts and transaction IDs. The alert **score** is the severity base (25/50/75/92) × confidence.

**Anomaly model.** An Isolation Forest (200 trees, contamination 2%, fixed seed) runs over 11 features:
- log amount;
- amount z-score relative to the sender's and the receiver's history;
- night-time flag;
- sender's 24-hour transaction count;
- time since the sender's previous transaction;
- receiver in-degree and sender out-degree;
- near-threshold flag and round-amount flag;
- 48-hour inflow-to-amount ratio (pass-through).

The output is a percentile rank. Each model alert lists the features with the most extreme robust z-scores. This explanation is labelled as an approximation, not an attribution.

**Risk fusion** per transaction:

```
rule_risk  = 1 − Π (1 − alert_score/100)            over rule alerts containing the transaction
model_risk = ml_weight × clip((percentile − 0.9)/0.1, 0, 1)        ml_weight = 0.35
risk       = 100 × (1 − (1 − rule_risk)(1 − model_risk))           flagged if risk ≥ 50
```

Because `ml_weight × 100 = 35 < 50`, **the model alone cannot flag a transaction**. A test enforces this. Account risk uses the same noisy-OR over alerts (weight 1.0 as primary account, 0.6 as an involved account). Entity risk is the maximum risk of its accounts.

**Three kinds of statements are kept separate** throughout the data model, UI and reports:
- `Alert(kind="rule")`: automated indicators;
- `Alert(kind="model")` / `anomaly_score`: model predictions;
- `CaseFinding`: investigator conclusions.

Nothing automated is ever called "fraud". Review decisions on alerts (acknowledged, dismissed, escalated) carry over to identical alerts in later runs via a fingerprint.

**Ground truth.** Synthetic files carry `is_fraud` and `fraud_pattern` columns. They are stored in `ground_truth_*` columns that the detection loader selects **only** for `evaluate()`. A test scrambles the labels and asserts that the scores are byte-identical.

### Design decision: why temporal cycle search instead of `nx.simple_cycles`

On payroll, rent and merchant graphs, structural cycles explode combinatorially (employer → employee → merchant → …), and most are meaningless because the money did not travel in that order. Constraining the search by time order, time span and amount similarity prunes the search to almost nothing and matches the investigative meaning of "the same funds came back". The run takes about 0.4 s on 15k transactions.

### Known limitations

- Rules are heuristic, US-threshold-centric and parameter-sensitive. The unusual-behaviour rule compares each transaction with the account's whole history, not strictly its prior history.
- The synthetic evaluation is circular (same authors), so it validates behaviour, not real-world efficacy.
- Detection is a batch over all transactions: about 3 s for 15k rows. The algorithms are near-linear apart from per-account loops; datasets in the millions would need incremental or partitioned runs.

## Graph (Module C)

`services/graph.py` aggregates transfers per (sender, receiver), with count, total, maximum risk, number flagged, and first/last timestamps. These aggregates are served as Cytoscape elements together with holder-entity nodes and "owns" edges.
- **Focus mode** expands breadth-first by N hops from an account.
- **Shortest path** uses NetworkX on the full aggregated digraph, either directed (money direction) or undirected (any connection).
- **Components** are weakly connected components of the subgraph of transactions with risk ≥ X.
- **Cycles** come from the detection alerts.
- **Follow the money** (`/accounts/{id}/flow`) aggregates upstream and downstream counterparties per level, up to 4 hops.
- **Exports:** JSON, GraphML and a client-side PNG.

## Entity resolution (Module E)

Candidate pairs are generated with blocking (same entity type + surname token, IP groups, reference groups, inverted counterparty index), so the work is not O(n²). The signals and their weights are:

| Signal | Weight |
|---|---|
| Exact name | 0.35 |
| Similar name (token-sort ratio ≥ 88) | 0.2 |
| Surname plus first initial | 0.15 |
| Shared IP (groups of ≤ 10) | 0.4 |
| Shared uncommon reference | 0.25 |
| Shared contact | 0.5 |
| Shared identifier | 0.6 |
| Repeated transfers (≥ 3) | 0.3 |
| Common uncommon counterparties | 0.2 |

The weights are combined by noisy-OR, with these rules:
- **Name-only confidence is capped at 0.35.**
- Purely behavioural pairs (repeated transfers or common counterparties only) are not suggested, because they are dominated by payroll and rent.
- A pair is a `possible_duplicate` only with a name signal plus an identity signal, or an exact name; otherwise it is `related`.
- Recompute refreshes only `suggested` links; verified and rejected decisions are kept.
- **Merging** requires a `verified possible_duplicate` link. It moves the accounts, records the alias and `merged_from`, and is audited.

## Evidence integrity (Module F)

- `LocalStorage.put` opens files with `O_EXCL` (`"xb"`), so it **never overwrites**. It hashes while streaming, enforces the size limit, then makes the file `chmod 0440`. Keys are normalised so path traversal cannot escape the root.
- Uploads are restricted by extension and sniffed against magic bytes (PDF, PNG, JPEG, GIF, WEBP, XLSX, DOCX) or validated as UTF-8 without NUL bytes (CSV, TXT, MD, JSON).
- **Custody chain:**
  ```
  event_hash = SHA-256(prev_hash | evidence_id | action | actor | timestamp | details | file_sha256)
  ```
  The first event uses a genesis hash of 64 zeros. `verify_chain` recomputes the chain, so editing, deleting or reordering any event is detected. Tests cover all three cases.
- **Verification** re-hashes the stored file and compares it with the hash recorded at upload. A mismatch sets the status to `failed` and appends a `verification_failed` event.
- **Recorded events:** uploaded, accessed (metadata view or inline preview), exported (download), verified or verification_failed, and metadata_modified (with old and new values).
- **Access control:** admins and reviewers can access all cases. Investigators can access only cases they created or are assigned to, and this applies to listing, viewing, downloading and editing.
- **S3:** `S3Storage` implements the same interface (existence check before upload, SHA-256 checksum), but **it has not been run against an S3 endpoint** in this repository's tests. To use it, install `boto3` in the API image and set `FL_STORAGE_BACKEND=s3`. Enable bucket Object Lock for true write-once semantics.

## Security

| Concern | Implementation |
|---|---|
| Authentication | JWT (HS256) issued by `/auth/login`; bcrypt (cost 12) hashes. A dummy hash is checked when the user is unknown, to reduce user-enumeration timing differences. Failed logins are audited. |
| Passwords | Minimum 10 characters with upper case, lower case and a digit. |
| Authorisation | Permission matrix in `core/deps.py` (`require("perm")`) plus case-level membership checks. Status transitions are enforced, and only reviewers or admins close cases or act on a case under review. |
| Validation | Pydantic schemas with length limits. Query parameters are typed and bounded. Upload type, size and content checks. |
| SQL injection | ORM / Core expressions only (bound parameters). |
| Errors | Validation errors return only field locations and messages, never the submitted values. Unhandled errors return a generic 500 and are logged server-side without request bodies. |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`. Evidence downloads add a restrictive CSP. |
| Secrets | Environment variables only. Outside development the app refuses to start with the default secret. |
| Audit | `audit_log` records logins, imports, detection runs, exports, case changes, evidence actions, settings and user changes. Readable by admins and reviewers. |
| Frontend | The token is kept in memory and `sessionStorage` (cleared when the tab closes). Nothing sensitive is logged to the console. |

Not implemented (would be needed for production): refresh-token rotation and revocation, MFA, rate limiting on login, encryption at rest for the storage volume, and TLS termination (place behind a TLS proxy).

## Frontend

React 18, TypeScript, Vite, Tailwind 3 and shadcn-style primitives written in `src/components/ui` (Radix Dialog for accessible modals and drawers).
- **Data:** TanStack Query handles fetching, caching and polling of running jobs.
- **Charts and graph:** Recharts and Cytoscape.js. Chart colours follow a CVD-validated categorical palette, and status colours (red, amber, green) are reserved for risk and integrity and always paired with a text label.
- **States:** every page has loading, error and empty states. All metrics come from the API.
