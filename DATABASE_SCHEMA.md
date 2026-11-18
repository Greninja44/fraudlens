# Database Schema

PostgreSQL 16, managed by Alembic (`backend/alembic/versions`). JSON columns are `JSONB` on PostgreSQL. All timestamps are `timestamptz` in UTC. Monetary amounts are `NUMERIC(18,2)`.

```
users ─┬─< import_batches
       ├─< cases ─┬─< case_assignees >─ users
       │          ├─< case_accounts   >─ accounts
       │          ├─< case_transactions >─ transactions
       │          ├─< case_notes / case_findings / case_events
       │          ├─< evidence ─< custody_events
       │          └─< reports
       └─< audit_log
entities ─< accounts ─< transactions (sender_account_id, receiver_account_id)
entities ─< entity_links >─ entities
detection_runs ─< alerts >─ accounts
system_settings (key/value)
```

## Core data

**users**: `id`, `email` (unique), `full_name`, `role` (`admin|investigator|reviewer`), `hashed_password` (bcrypt), `is_active`, `created_at`, `last_login_at`.

**import_batches**: one row per uploaded file.
- `filename`, `file_type`, `file_sha256`, `file_size`
- `status` (`pending|queued|processing|completed|failed`)
- `rows_total|imported|duplicate|invalid|with_warnings`
- `column_mapping` (JSONB), `errors` (JSONB, up to 500 row issues), `summary` (JSONB: columns, suggested mapping, sample rows, storage key)
- `has_ground_truth`, `created_by_id`, timestamps

**entities**: a real-world party.
- `entity_type` (`individual|business|unknown`), `name`, `normalized_name` (indexed)
- `attributes` (JSONB: aliases, merged_from, optional emails/phones/identifiers)
- `risk_score`
- `merged_into_id` (self-FK; set only by an investigator-approved merge)

**accounts**: `account_number` (unique), `bank`, `holder_name`, `entity_id` → entities, `first_seen`, `last_seen`, `txn_count`, `total_in`, `total_out`, `risk_score`, `risk_level`, `alert_count`, `patterns` (JSONB list).

**transactions**:

| Column | Notes |
|---|---|
| `txn_id` | unique external ID (generated from a content hash if missing) |
| `sender_account_id`, `receiver_account_id` | → accounts |
| `sender_name`, `receiver_name`, `amount`, `currency`, `timestamp`, `txn_type`, `bank`, `reference`, `ip_address`, `status` | normalised source fields |
| `import_batch_id`, `data_warnings` (JSONB) | provenance and data-quality notes |
| `risk_score`, `risk_level`, `anomaly_score`, `is_flagged`, `patterns` (JSONB) | outputs of the latest detection run |
| `ground_truth_label`, `ground_truth_pattern` | synthetic evaluation labels; **never read by detectors** |

Indexes: `(sender_account_id, timestamp)`, `(receiver_account_id, timestamp)`, `timestamp`, `risk_score`, `is_flagged`, `reference`, `ip_address`, `import_batch_id`.

## Detection

**detection_runs**: `status`, `params` (JSONB snapshot of the settings used), `stats` (JSONB), `evaluation` (JSONB or null), `error`, `triggered_by_id`, `started_at`, `completed_at`.

**alerts**: one automated indicator.

| Column | Notes |
|---|---|
| `run_id` | → detection_runs, cascade delete |
| `kind` | `rule` or `model` |
| `pattern`, `severity`, `confidence`, `score`, `title` | classification and strength |
| `reasons` | JSONB list of strings |
| `details` | JSONB: account_ids, path, amounts, fingerprint, model top_features … |
| `account_id` | primary account |
| `transaction_ids` | JSONB list |
| `first_txn_at`, `last_txn_at`, `total_amount` | period and value covered |
| `review_status`, `reviewed_by_id`, `reviewed_at` | review decisions, carried over between runs via `details.fingerprint` |

**system_settings**: `key` (PK), `value` (JSONB), `updated_by_id`, `updated_at`. Detection thresholds live under the key `detection`.

## Entity resolution

**entity_links**: `entity_a_id < entity_b_id` (unique pair), `link_type` (`possible_duplicate|related`), `confidence`, `signals` (JSONB list `{signal, weight, detail}`), `status` (`suggested|verified|rejected`), `verified_by_id`, `verified_at`, `note`.

## Case management

| Table | Columns |
|---|---|
| **cases** | `case_number` (unique, `FC-YYYY-NNNN`), `title`, `summary`, `objectives`, `status`, `priority`, `created_by_id`, `created_at`, `updated_at`, `closed_at` |
| **case_assignees** | PK (`case_id`, `user_id`) |
| **case_accounts** | PK (`case_id`, `account_id`), `role` (`subject|counterparty|related`), `note`, `added_by_id`, `added_at` |
| **case_transactions** | PK (`case_id`, `transaction_id`), `note`, `added_by_id`, `added_at` |
| **case_notes** | `case_id`, `author_id`, `body`, `created_at` (investigator observations) |
| **case_findings** | `case_id`, `author_id`, `title`, `description`, `status` (`draft|confirmed|refuted`), `related_alert_ids` (JSONB). The **only** store of conclusions. |
| **case_events** | `case_id`, `event_type`, `description`, `actor_id`, `payload` (JSONB), `created_at` (activity log feeding the timeline and report) |

## Evidence

**evidence**:
- `evidence_code` (unique, `EV-YYYY-NNNNNN`), `case_id`
- `original_filename`, `storage_key` (write-once object), `content_type`, `file_type`, `category`, `size_bytes`
- `sha256` (indexed), `description`
- `verification_status` (`unverified|verified|failed`), `last_verified_at`, `uploaded_by_id`, `uploaded_at`

**custody_events**: append-only.
- `evidence_id`, `action` (`uploaded|accessed|exported|verified|verification_failed|metadata_modified`)
- `actor_id`, `actor_email`, `details` (JSONB), `ip_address`, `created_at`
- `prev_hash`, `event_hash`, where `event_hash = SHA-256(prev_hash, evidence_id, action, actor, timestamp, details, file sha256)`

**reports**: `case_id`, `format`, `storage_key`, `filename`, `sha256`, `size_bytes`, `generated_by_id`, `generated_at`.

## Audit

**audit_log**: `actor_id`, `actor_email`, `action` (indexed, for example `case.status_changed`), `resource_type`, `resource_id`, `details` (JSONB), `ip_address`, `created_at` (indexed).
