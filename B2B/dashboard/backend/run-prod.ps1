# Production run for api.bitcodingsolutions.com.
#
# Sets hardened defaults:
#   - DASHBOARD_DOCS=0          hides /docs, /redoc, /openapi.json
#   - DASHBOARD_REQUIRE_AUTH=1  enforce X-API-Key on writes / actions
#   - LINKEDIN_TRACKING_BASE_URL  points outgoing tracking pixels at the
#     public endpoint
# Uvicorn still binds 127.0.0.1 — front-door traffic must come through
# the fronting reverse proxy / tunnel, not raw TCP.

$env:LINKEDIN_TRACKING_BASE_URL = "https://api.bitcodingsolutions.com"
$env:DASHBOARD_DOCS = "0"
$env:DASHBOARD_REQUIRE_AUTH = "1"

# If you're fronting with a public tunnel (Cloudflare, ngrok, etc.) add
# its origin to the CORS allowlist so the remote dashboard can still POST.
# Comma-separated. Leave unset in single-machine deployments.
# $env:DASHBOARD_EXTRA_ORIGINS = "https://app.bitcodingsolutions.com"

# Rate limit per IP per minute (default 120 in code).
# $env:DASHBOARD_RATE_LIMIT = "60"

# DASHBOARD_API_KEY stays in a gitignored .api_key file at the project
# root. Set this env var to override if you want a static key across
# redeploys.
# $env:DASHBOARD_API_KEY = "..."

python -m uvicorn main:app --host 127.0.0.1 --port 8900
