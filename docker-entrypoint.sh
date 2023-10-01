#!/bin/sh
# One command, no manual steps: migrate, collect static, seed, then serve.
set -e

echo "==> Waiting for the database"
python - <<'PY'
import os, time
import dj_database_url
import psycopg2

config = dj_database_url.config(default=os.environ["DATABASE_URL"])
for attempt in range(60):
    try:
        psycopg2.connect(
            dbname=config["NAME"], user=config["USER"], password=config["PASSWORD"],
            host=config["HOST"], port=config["PORT"] or 5432,
        ).close()
        break
    except Exception as exc:
        if attempt == 59:
            raise
        print(f"   database not ready ({exc.__class__.__name__}); retrying")
        time.sleep(1)
PY

echo "==> Applying migrations"
python manage.py migrate --noinput

if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
  echo "==> Collecting static files"
  python manage.py collectstatic --noinput >/dev/null
fi

if [ "${SEED_DEMO_DATA:-1}" = "1" ]; then
  echo "==> Seeding demo data"
  python manage.py seed_demo
fi

echo "==> Starting: $*"
exec "$@"
