web: MALLOC_ARENA_MAX=2 gunicorn --chdir src app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --max-requests 12 --max-requests-jitter 4
