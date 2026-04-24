# Deploy & Security Checklist

This repository contains a lightweight Flask app. Follow these steps when deploying to production.

## Required environment variables
- `SECRET_KEY` - Flask secret key (keep secret)
- `RATE_LIMIT_STORAGE_URI` - storage for `flask-limiter` (e.g. `redis://:password@host:6379/0`). If omitted, an in-memory limiter is used (not recommended in multi-instance).
- `RATE_LIMIT_DEFAULT` - limit string (e.g. `60 per minute`).
- `PRIVATE_STORAGE_ID` - (optional) Google Sheets ID for remote private submissions.
- `PRIVATE_STORAGE_CREDENTIALS_FILE` or `PRIVATE_STORAGE_CREDENTIALS_JSON` - credentials for Google API.
- `PRIVATE_STORAGE_ALLOW_VERCEL_LOCAL_FALLBACK` - `0` or `1`. If `1`, app will fallback to local storage when remote fails. Default in sample: `1`.
- `PRIVATE_STORAGE_STRICT_REMOTE` - `0` or `1`. If `1`, failures to write to remote storage will cause an error instead of falling back.
- `VERCEL` - set to `1` if deploying on Vercel.

## Recommended production setup
1. Use a Redis instance and set `RATE_LIMIT_STORAGE_URI` to the Redis URI.
2. Keep `PRIVATE_STORAGE_STRICT_REMOTE=0` for resilience, unless you specifically require remote-only writes.
3. Ensure `SECRET_KEY` is a strong random value.
4. TLS must be enforced by your hosting provider (HSTS header is already set by the app).

## Quick deploy steps (example)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export RATE_LIMIT_STORAGE_URI="redis://:password@redis-host:6379/0"
# configure PRIVATE_STORAGE_* 
python -m flask run --host=0.0.0.0 --port=8000
```

## Smoke tests
Run the included smoke test script to validate endpoints locally:

```bash
python scripts/smoke_test.py
```

## CI
We include a simple GitHub Actions workflow `.github/workflows/ci.yml` that installs dependencies and runs the smoke test.

## Security notes
- Images are proxied via `/img_proxy` to prevent client IP leakage to third parties.
- CSRF protection is enabled via `Flask-WTF` and the frontend uses `/api/csrf-token`.
- Use Redis for rate limiting in multi-instance deployments.
- Consider adding monitoring/alerting around failed remote submissions to detect persistent errors.
