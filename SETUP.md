# Setup

## Requirements

| Path | Needs |
|---|---|
| Docker (recommended) | Docker with Compose v2 |
| Local development | Python 3.12 + [uv](https://docs.astral.sh/uv/), Node.js 22, Docker (for PostgreSQL) |

Pinned dependency versions are recorded in `backend/uv.lock` and `frontend/package-lock.json`.

## Option 1 — Docker Compose

```bash
cp .env.example .env
# Edit .env: set FL_SECRET_KEY (required unless FL_ENV=development) and the Postgres password.
docker compose up --build -d
docker compose logs -f api      # watch migrations + demo seeding on first start
```

| Service | URL |
|---|---|
| Web UI (nginx, proxies /api) | http://localhost:5180 |
| API + interactive docs | http://localhost:8100/api/docs |
| PostgreSQL | localhost:5440 |

On start-up the API container runs `alembic upgrade head`. When `FL_SEED_DEMO=true`, it also creates the demo users and loads `datasets/synthetic_transactions.csv`. This runs detection and computes entity links, and is skipped if transactions already exist. To start empty, set `FL_SEED_DEMO=false` and create an admin with:

```bash
docker compose exec api python -m app.cli create-user admin@example.org "Admin Name" admin
```

Volumes: `pgdata` (database) and `storage` (evidence, original import files and reports). Back up both together, because evidence metadata and hashes live in the database while the files live in storage.

Ports can be changed with `FL_DB_PORT`, `FL_API_PORT` and `FL_WEB_PORT`.

## Option 2 — Local development

```bash
# 1. Database
docker compose up -d db

# 2. Backend
cd backend
uv sync                                  # creates .venv with Python 3.12
uv run alembic upgrade head
uv run python -m app.cli seed-users      # demo users (development only)
uv run python -m app.cli load-demo       # import synthetic data + detection + entity links
uv run uvicorn app.main:app --port 8100 --reload

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev                              # http://localhost:5180, proxies /api to :8100
```

Other CLI commands:

```bash
uv run python -m app.cli detect                         # re-run detection
uv run python -m app.cli create-user EMAIL "Name" ROLE  # prompts for a password
uv run python -m app.synthetic --out ../datasets --seed 42   # regenerate datasets
```

## Configuration

All settings are environment variables with the `FL_` prefix, read from the environment or a `.env` file in the project root or `backend/`. See `.env.example`.

| Variable | Default | Notes |
|---|---|---|
| `FL_ENV` | `development` | Any other value **requires** `FL_SECRET_KEY` to be set; the app refuses to start otherwise. |
| `FL_SECRET_KEY` | dev placeholder | JWT signing key. Use ≥ 32 random bytes. |
| `FL_DATABASE_URL` | `postgresql+psycopg://fraudlens:fraudlens@localhost:5440/fraudlens` | SQLAlchemy URL. |
| `FL_ACCESS_TOKEN_MINUTES` | 480 | Session length. |
| `FL_CORS_ORIGINS` | `["http://localhost:5180"]` | JSON list. |
| `FL_STORAGE_BACKEND` | `local` | `local` or `s3`. |
| `FL_STORAGE_DIR` | `backend/var/storage` | Local object storage root. |
| `FL_S3_BUCKET`, `FL_S3_ENDPOINT_URL`, `FL_S3_PREFIX` | — | For `s3`; requires `boto3` (not installed by default; see ARCHITECTURE.md). |
| `FL_MAX_IMPORT_MB` / `FL_MAX_EVIDENCE_MB` | 200 / 50 | Upload limits. |
| `FL_DATASETS_DIR` | `datasets/` | Sample datasets offered for download. |
| `FL_SEED_DEMO`, `FL_DEMO_PASSWORD` | — | Docker first-start seeding. |

Detection thresholds are not environment variables. Administrators change them in **Settings → Detection**, and the values are stored in the `system_settings` table.

## Tests

```bash
cd backend
uv run pytest                                   # 66 tests, temporary SQLite DB (~30 s)
FL_TEST_DATABASE_URL=postgresql+psycopg://fraudlens:fraudlens@localhost:5440/fraudlens_test uv run pytest   # same suite on PostgreSQL

cd frontend
npm test                                        # Vitest
npm run typecheck && npm run build
```

The `fraudlens_test` database is created automatically by `scripts/init-test-db.sql` the first time the `db` container initialises its volume. If your volume predates that, create it with `docker compose exec db createdb -U fraudlens fraudlens_test`.

## Troubleshooting

- **`could not open file ... Read-only file system` from PostgreSQL**: Docker Desktop's VM disk became read-only, which was seen once during development after an interrupted image pull. Restart Docker Desktop.
- **Port already in use**: change the `FL_*_PORT` values in `.env`.
- **Large imports**: CSV is streamed in 20,000-row chunks and committed per chunk. The nginx proxy accepts bodies up to 210 MB.
