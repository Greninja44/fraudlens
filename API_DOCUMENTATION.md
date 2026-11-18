# API Documentation

Base path: `/api`. The interactive OpenAPI docs are at **`/api/docs`** and the schema at `/api/openapi.json`.

All endpoints except `POST /auth/login` and `GET /health` require `Authorization: Bearer <token>`. Errors return `{"detail": "..."}`. Validation errors return `{"detail": "Invalid request", "errors": [{"loc": [...], "msg": "..."}]}`, and submitted values are never echoed back.

## Roles and permissions

| Permission | admin | investigator | reviewer |
|---|:-:|:-:|:-:|
| data:import, detection:run | ✓ | ✓ | |
| alerts:review, entities:verify, evidence:verify, reports:generate | ✓ | ✓ | ✓ |
| entities:merge, cases:write, evidence:upload, evidence:edit | ✓ | ✓ | |
| cases:review (close, or decide on a case pending review) | ✓ | | ✓ |
| users:manage, settings:manage | ✓ | | |
| audit:read | ✓ | | ✓ |

**Case scope:** investigators can see and modify only cases they created or are assigned to, including the evidence and reports of those cases. Admins and reviewers can read all cases.

## Authentication & users

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | `{email, password}` → `{access_token, token_type, user}` |
| GET | `/auth/me` | Current user, including the `permissions` list |
| POST | `/auth/change-password` | `{current_password, new_password}` |
| GET | `/users` | List users (any authenticated user; needed for assignment) |
| POST | `/users` | *users:manage*. `{email, full_name, role, password}` |
| PATCH | `/users/{id}` | *users:manage*. `{full_name?, role?, is_active?, password?}`. You cannot demote or deactivate yourself. |

## Ingestion

| Method | Path | Description |
|---|---|---|
| POST | `/imports/preview` | *data:import*. Multipart `file` (.csv/.xlsx/.json). Stores the original and returns `{id, columns, suggested_mapping{field: {column, confidence, method}}, sample, fields, previously_imported}` |
| POST | `/imports/{id}/commit` | `{mapping: {field: column}, skip_content_duplicates?: true}` → 202. Validates the required mappings, then imports in the background. |
| GET | `/imports` | Import history (paginated) |
| GET | `/imports/{id}` | Status, counts (`rows_total/imported/duplicate/invalid/with_warnings`) and row-level `errors` |
| GET | `/datasets` | Bundled synthetic datasets and their dataset card |
| GET | `/datasets/{name}` | Download one of the listed dataset files |

Canonical mapping fields:
- **Required:** `sender_account`, `receiver_account`, `amount`, `timestamp`.
- **Optional:** `txn_id`, `sender_name`, `receiver_name`, `currency`, `txn_type`, `bank`, `receiver_bank`, `reference`, `ip_address`, `status`, `ground_truth_label`, `ground_truth_pattern`.

## Transactions & accounts

| Method | Path | Description |
|---|---|---|
| GET | `/transactions` | Filters: `search, account_id, date_from, date_to, min_amount, max_amount, min_risk, flagged, pattern, txn_type, status, import_batch_id`. `sort` = `timestamp\|amount\|risk_score\|txn_id`, `order` = `asc\|desc`, `page`, `page_size` (≤ 500) |
| GET | `/transactions/facets` | Distinct types and statuses, date range, maximum amount |
| GET | `/transactions/export` | CSV with the same filters (audited) |
| GET | `/transactions/{id}` | Detail with the explaining `alerts`, the `cases` it belongs to, and the `ground_truth` note (synthetic only) |
| GET | `/accounts` | `search, min_risk, risk_level, pattern, sort, page` |
| GET | `/accounts/lookup?number=` | Find an account by its number |
| GET | `/accounts/{id}` | Profile: entity, sibling accounts, alerts, top sources and destinations, monthly flows, cases |
| GET | `/accounts/{id}/flow` | Follow the money: `direction=both\|upstream\|downstream`, `depth` 1–4, `min_amount`, dates → `{upstream[], downstream[], accounts{}}` |

## Detection

| Method | Path | Description |
|---|---|---|
| GET | `/detection/patterns` | Pattern code → label |
| POST | `/detection/runs` | *detection:run*. Starts a run (202), or returns 409 if one is already running |
| GET | `/detection/runs`, `/detection/runs/{id}` | Status, `params`, `stats` (alerts by pattern, flagged count and value, timings) and `evaluation` (ground-truth metrics, or null) |
| GET | `/alerts` | Latest run by default. Filters: `run_id, kind=rule\|model, pattern, severity (comma list), review_status, account_id, min_score, date_from, date_to`. `sort` = `score\|recent\|amount` |
| GET | `/alerts/summary` | Counts by pattern, severity and review status |
| GET | `/alerts/{id}` | Detail with transactions and involved accounts |
| POST | `/alerts/{id}/review` | *alerts:review*. `{review_status: new\|acknowledged\|dismissed\|escalated, note?}` |
| GET | `/dashboard` | Totals, last run, daily activity, alerts by pattern, recent alerts and cases, account risk distribution, top accounts |

## Network graph

| Method | Path | Description |
|---|---|---|
| GET | `/graph` | `account_id` (focus), `depth` 1–4, `date_from, date_to, min_amount, min_risk, suspicious_only, include_entities, limit` → `{nodes, edges, truncated, stats}` |
| GET | `/graph/path?source=&target=&directed=true` | Shortest path with per-hop totals |
| GET | `/graph/components?min_risk=50` | Connected clusters formed by risky transactions |
| GET | `/graph/cycles` | Circular flows from the latest run |
| GET | `/graph/export?format=json\|graphml` | Same filters as `/graph` (audited) |

Node types are `account`, `individual`, `business` and `unknown`. Edge types are `transfer` (aggregated: `count, total, max_risk, flagged, first, last`) and `owns`.

## Entities

| Method | Path | Description |
|---|---|---|
| GET | `/entities` | `search, entity_type, min_risk, page` |
| GET | `/entities/{id}` | Entity with its accounts and links |
| GET | `/entities/links` | `status, link_type, min_confidence, entity_id, page`. Each link includes `signals[{signal, weight, detail}]` |
| POST | `/entities/links/recompute` | *entities:merge*. Refreshes suggestions; investigator decisions are preserved |
| POST | `/entities/links/{id}/decision` | *entities:verify*. `{status: verified\|rejected\|suggested, note?}` |
| POST | `/entities/links/{id}/merge` | *entities:merge*. `{keep_entity_id}`. Returns 409 unless the link is a verified possible duplicate |

## Timeline

| Method | Path | Description |
|---|---|---|
| GET | `/timeline` | `case_id` and/or `account_id` (one is required), `date_from, date_to, kinds` (comma list of `transaction,detection,evidence,note,case`), `flagged_only, limit` |
| GET | `/timeline/export?format=csv\|json` | Same parameters (audited) |

## Cases

| Method | Path | Description |
|---|---|---|
| POST | `/cases` | *cases:write*. `{title, summary, objectives, priority, assignee_ids[], account_ids[], transaction_ids[], alert_ids[]}`. When alert IDs are given, each alert's primary account is added as `subject`, its other involved accounts as `related`, and its transactions are linked. |
| GET | `/cases` | `status` (comma list), `priority, search, mine, page` |
| GET | `/cases/{id}` | Full case: accounts, transactions, notes, findings, events, evidence, reports, `allowed_transitions`, progress |
| PATCH | `/cases/{id}` | `{title?, summary?, objectives?, priority?}` |
| POST | `/cases/{id}/status` | `{status, comment?}`. Transitions are enforced (see below) |
| PUT | `/cases/{id}/assignees` | `{user_ids[]}`. Reviewers cannot be assignees |
| POST / DELETE | `/cases/{id}/accounts[/{account_id}]` | `{account_ids[], role: subject\|counterparty\|related, note?}` |
| POST / DELETE | `/cases/{id}/transactions[/{txn_id}]` | `{transaction_ids[], alert_id?, include_accounts?}` |
| POST | `/cases/{id}/notes` | `{body}` (case members, and reviewers) |
| POST | `/cases/{id}/findings` | `{title, description, status: draft\|confirmed\|refuted, related_alert_ids[]}` |
| PATCH | `/cases/{id}/findings/{fid}` | `{status}` |
| GET | `/cases/{id}/export?format=json\|csv` | Investigation JSON, or transaction summary CSV |

**Status transitions:**
- `open` → `under_investigation`, `escalated` or `closed`
- `under_investigation` → `pending_review`, `escalated` or `open`
- `pending_review` → `closed`, `under_investigation` or `escalated`
- `escalated` → `under_investigation`, `pending_review` or `closed`
- `closed` → `under_investigation` (reopen)

Moving to `closed`, or acting on a case that is `pending_review` or `closed`, requires *cases:review*. A closed case is read-only until reopened.

## Evidence

| Method | Path | Description |
|---|---|---|
| POST | `/cases/{id}/evidence` | *evidence:upload*. Multipart `file`, `description`, `category`. Returns `evidence_code` and `sha256` |
| POST | `/cases/{id}/evidence/transaction-extract` | Preserves the case's transaction records as a CSV evidence item |
| GET | `/evidence` | `case_id, search` (code, filename, description or hash), `verification_status, file_type, page` |
| GET | `/evidence/{id}` | Metadata, custody events and chain verification (records an `accessed` event) |
| GET | `/evidence/{id}/download?inline=` | Original bytes, with an `X-Evidence-SHA256` header (records `exported`, or `accessed` when inline) |
| POST | `/evidence/{id}/verify` | *evidence:verify*. Re-hashes the stored file → `{hash_match, expected_sha256, actual_sha256, custody_chain}` |
| PATCH | `/evidence/{id}` | *evidence:edit*. `{description?, category?}`. Records `metadata_modified` with old and new values |
| GET | `/evidence-categories` | Allowed categories, extensions and size limit |

## Reports

| Method | Path | Description |
|---|---|---|
| POST | `/cases/{id}/reports` | *reports:generate*. Builds the PDF and stores it with its SHA-256 |
| GET | `/reports` | Reports visible to the caller |
| GET | `/reports/{id}/download` | PDF, with an `X-Report-SHA256` header |

## Administration

| Method | Path | Description |
|---|---|---|
| GET | `/settings/detection` | `{values, defaults, help}` |
| PUT | `/settings/detection` | *settings:manage*. Partial update; unknown keys or negative values return 422 |
| GET | `/settings/system` | Environment, storage backend, limits, whether the default secret is still in use |
| GET | `/audit` | *audit:read*. `action` prefix, `actor_id, date_from, page` |
| GET | `/health` | `{status, database}` (no authentication) |

## Example

```bash
TOKEN=$(curl -s -X POST localhost:8100/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"investigator@fraudlens.local","password":"FraudLens-Demo-2026"}' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8100/api/alerts?pattern=circular&page_size=1" | jq '.items[0] | {title, reasons}'
```
