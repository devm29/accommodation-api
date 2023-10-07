release: python manage.py migrate --noinput
web: gunicorn bnbu_backend_api.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 120 --log-file -
worker: celery -A bnbu_backend_api worker --pool=threads --loglevel=info -E --concurrency=${CELERY_CONCURRENCY:-2}
