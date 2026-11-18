#!/bin/sh
set -e
echo "[entrypoint] applying database migrations"
alembic upgrade head
if [ "${FL_SEED_DEMO:-false}" = "true" ]; then
  echo "[entrypoint] seeding demo users and synthetic data (FL_SEED_DEMO=true)"
  python -m app.cli seed-users
  python -m app.cli load-demo --if-empty
fi
exec "$@"
