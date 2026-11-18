# FraudLens — Financial Fraud Investigation Platform

FraudLens is an end-to-end platform for financial investigators. It ingests transaction data, scores it with explainable detection rules and an anomaly model, visualises money flows as an interactive network, links related entities, and supports the full investigation workflow: case management, a tamper-evident evidence vault and PDF reporting.

> **Development data is synthetic.** Every person, company, bank, account and IP address in the bundled datasets is fictitious (IPs use RFC 5737 documentation ranges). The platform is not connected to any banking system or law-enforcement database.

## What it does

| Module | Highlights |
|---|---|
| **A. Ingestion** | CSV / XLSX / JSON. Automatic column mapping (exact + fuzzy, globally optimal assignment) with a manual override. Row validation, normalisation, missing-value handling, duplicate detection by transaction ID (within the file and across imports), plus content duplicates: the same accounts and amount within 60 s under another ID, which is typical of overlapping bank exports. These are skipped by default and the skip is optional. Chunked processing and bulk inserts. Import history with row-level error reports. The original file is preserved with its SHA-256. |
| **B. Detection** | Seven explainable rule families: structuring and smurfing, rapid pass-through, temporal circular flows, unusual behaviour, high velocity, shared-infrastructure and counterparty networks, and dormant reactivation. An Isolation Forest scores 11 behavioural features. 0–100 risk fusion, where **a model prediction alone can never flag a transaction**. Every alert carries a severity, a confidence and written reasons. Evaluation against synthetic ground truth. |
| **C. Network** | Cytoscape graph of accounts, individuals and businesses. Directed, amount-weighted edges, risk colouring and flagged-edge highlighting. Focus and expand by N hops, filters (date, amount, risk, flagged), shortest path (directed or undirected), temporal cycles, risk-filtered connected components, and export (JSON, GraphML, PNG). |
| **D. Timeline** | Transactions, detection events, evidence custody, notes and case activity merged chronologically. Event map swimlanes, date filtering, transaction drill-down, event correlation (± window, shared accounts), CSV/JSON export. |
| **E. Entities** | One entity per account holder, with **no automatic merging**. Suggested links come from exact, similar or initial names, shared IPs, shared references, shared contacts or identifiers, repeated transfers and common counterparties. Each signal is explained and combined into a confidence score. Name-only suggestions are capped at low confidence. Investigators verify or reject links, and merging is allowed only on verified duplicates. |
| **F. Evidence** | Write-once, read-only storage behind an S3-ready interface. Uploads are sniffed against magic bytes and size-limited, and SHA-256 is recorded at upload. A **hash-chained chain of custody** records upload, access, export, verification and metadata changes. Integrity re-verification detects tampered files and tampered logs. Access is controlled per case. |
| **G. Cases** | Case numbers; Open → Under Investigation → Pending Review → Closed / Escalated, with enforced transitions (only reviewers or admins close cases). Assignees, subject and related accounts, transactions (or a whole alert), notes, investigator findings (draft, confirmed or refuted), an activity log and a progress checklist. |
| **H. Reports** | PDF with all 12 required sections, network and timeline figures, evidence hashes and custody status, and a clear separation between automated indicators, model predictions and investigator conclusions. Also CSV transaction summaries and JSON investigation exports. |

The platform also provides JWT authentication, bcrypt password hashing, three roles (admin, investigator, reviewer) with a permission matrix, case-level access control, a full audit log, admin-tunable detection thresholds, sanitised error responses and security headers.

## Quick start (Docker)

```bash
cp .env.example .env          # set FL_SECRET_KEY for anything beyond local use
docker compose up --build     # db + api + web; first start migrates and loads demo data
```

Open **http://localhost:5180** and sign in as `investigator@fraudlens.local` / `FraudLens-Demo-2026`. Other roles are `admin@`, `reviewer@` and `investigator2@`, with the same development password set by `FL_DEMO_PASSWORD`. Then follow **[DEMO_GUIDE.md](DEMO_GUIDE.md)**.

For local development without Docker images, see **[SETUP.md](SETUP.md)**.

## Measured detection quality (synthetic data)

Detection run on `datasets/synthetic_transactions.csv` (14,864 transactions, 240 labelled suspicious), default settings, about 3 s on an Apple M-series laptop:

| Decision | Precision | Recall | F1 |
|---|---|---|---|
| Flagged (fused risk ≥ 50) | 0.846 | 0.963 | 0.90 |
| Any rule indicator | 0.439 | 1.000 | 0.61 |
| Isolation Forest outlier alone | 0.481 | 0.596 | 0.53 |

Risk-score ROC-AUC is 0.999; the anomaly model alone has a ROC-AUC of 0.967. Recall per pattern: circular 20/20, structuring 37/37, dormant 19/19, high-velocity 99/100, smurfing 23/25, rapid movement 33/39.

**Interpret with care.** The generator and the detectors were written by the same team, so these figures are a regression check that the rules do what they are designed to do. They are not an estimate of real-world performance. They are re-computed on every run and shown in the Detection Center.

## Documentation

| File | Contents |
|---|---|
| [SETUP.md](SETUP.md) | Installation, configuration, running locally and in Docker, tests |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, detection design, risk fusion, evidence integrity, security, decisions and limitations |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | Every endpoint, permissions and examples (interactive docs at `/api/docs`) |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | Tables, relationships and indexes |
| [DEMO_GUIDE.md](DEMO_GUIDE.md) | The 10-step demonstration investigation |

## Repository layout

```
backend/            FastAPI app (app/), Alembic migrations, pytest suite
  app/api/          HTTP routes                  app/services/   ingestion, graph, entities, evidence, timeline, reports
  app/detection/    rules.py, ml.py, engine.py   app/synthetic.py  dataset generator
frontend/           React + TypeScript + Vite + Tailwind; Cytoscape.js and Recharts
datasets/           synthetic datasets + dataset_card.json (ground-truth summary, featured ring)
docker-compose.yml  db (PostgreSQL 16), api, web (nginx)
```

## Tests

66 backend tests (pytest) cover ingestion, validation, duplicates, every detection rule (including negative cases), end-to-end detection quality, ground-truth isolation, entity linking, graph analysis, evidence hashing, tamper detection, custody chain, authentication, authorisation and the full API workflow including PDF generation. 7 frontend tests (Vitest) also run. The backend suite passes on both SQLite and PostgreSQL. See [SETUP.md](SETUP.md#tests).
