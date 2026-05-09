#!/bin/sh
set -e

# Wait for DB
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER"; do
  echo "Waiting for postgres..."
  sleep 1
done

mkdir -p media static/css

if [ -f package.json ]; then
  npm install --no-fund --no-audit >/dev/null 2>&1 || true
  npx tailwindcss -i ./static_src/input.css -o ./static/css/tailwind.css --minify || true
fi

python manage.py migrate
python manage.py collectstatic --noinput
exec uvicorn srs_app.asgi:application --host 0.0.0.0 --port 8000 --lifespan=off
