Title: Security: image proxy, rate limiting, CSRF protection, and CI smoke tests

Summary:
This branch hardens the application against SSRF, hotlinking and abuse:

- Adds image proxy endpoint `/img_proxy` with caching and size/type limits (prevents client IP leakage and reduces SSRF vectors).
- Adds `ProxyFix` and changes to `_obter_ip_real()` to avoid trusting `X-Forwarded-For` directly.
- Integrates `flask-limiter` for rate limiting (configurable via `RATE_LIMIT_STORAGE_URI`, fallback `memory://`).
- Adds `Flask-WTF` CSRF protection and endpoint `/api/csrf-token` for JS clients.
- Updates templates and `static/app.js` to fetch and use CSRF token in AJAX requests.
- Adds `.env.sample`, `README_DEPLOY.md`, `scripts/smoke_test.py`, and a GitHub Actions workflow `.github/workflows/ci.yml` that runs the smoke tests.
- Adds fallback behavior in `private_store.save_submission` to use local storage if remote write fails (unless `PRIVATE_STORAGE_STRICT_REMOTE=1`).

Testing performed:
- Installed dependencies locally and ran smoke tests (`scripts/smoke_test.py`) — all key endpoints passed.
- Ran targeted tests for header spoofing and SSRF; rate limiting properly enforces configured limits and SSRF to localhost is blocked.

How to create PR locally (recommended):

1. Install GitHub CLI (optional) and create PR:

```bash
# if gh is installed and authenticated
gh pr create --title "Security: image proxy, rate limiting, CSRF" --body-file PR_DESCRIPTION.md --base main
```

2. Or create PR via GitHub UI: the branch `security/csrf-rate-limiter` is already pushed to the remote `origin`.

Notes for reviewers:
- Review `app.py` security headers and CSP changes (img-src now restricted to 'self').
- Confirm Redis configuration for `RATE_LIMIT_STORAGE_URI` in production.
- Check `PRIVATE_STORAGE_*` env var behavior if you rely on remote storage.
