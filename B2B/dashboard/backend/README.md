# B2B Outreach — Dashboard Backend

FastAPI app that powers the B2B outreach dashboard (Marcel pipeline +
grab sources + LinkedIn). Runs against a local SQLite leads DB and a
local Claude bridge on `:8766`.

## Layout

```
backend/
  app/                       # New, structured FastAPI app
    main.py                    # FastAPI factory, middleware, router includes, lifespan
    config.py                  # pydantic-settings (env-driven)
    db.py                      # sqlite helpers (q_one / q_all / conn)
    deps.py                    # auth dependencies
    auth/
      ms.py                    # Entra JWT validation + Global Admin check
      api_key.py                 # Legacy X-API-Key
      router.py                  # /api/auth/config, /api/auth/me
    schemas/                   # Pydantic request/response models
    routers/                   # Per-domain HTTP routers
      overview.py                # /api/overview, /api/funnel, /api/daily-activity, /api/stats, /api/health
      leads.py                   # /api/leads, /api/lead/{id}, /api/industries, /api/hot-leads, /api/recent-sent
      replies.py                 # /api/replies/handle
      sources.py                 # /api/sources, /api/sources/{id}/leads, facets, …
      source_actions.py          # /api/sources/{id}/scrape | enrich | collect | campaign | …
      batches.py                 # /api/batches, /api/batches/files, /api/campaigns/batches, …
      actions.py                 # /api/actions/* (Marcel pipeline)
      jobs.py                    # /api/jobs[/...]
      bridge.py                  # /api/bridge-health, /api/actions/start-bridge
    services/                  # Business logic shared by routers
      jobs.py                    # JOBS dict, start_job, start_chain_job, parse_progress
      schedules.py               # daily auto-collect scheduler
      batch_export.py            # export-batch core, batch status, path resolution
      sources.py                 # in-memory source registry
      bridge.py                  # bridge probe + launcher
      outlook.py                 # COM check
      preflight.py               # bundles all the gates
      scrape_args.py             # schema -> CLI flag translator
  main.py                    # Compatibility shim → re-exports app.main:app
  sources_api.py             # Compatibility shim → re-exports new sources router
  linkedin_*.py              # LinkedIn modules (untouched in this refactor)
```

## Run

```bash
cd dashboard/backend
uv run uvicorn app.main:app --reload --port 8000
# or, for back-compat:
uv run uvicorn main:app --reload --port 8000
```

## Authentication

The backend accepts **two** schemes; either is sufficient for any
protected endpoint.

1. **Microsoft Entra Bearer token** (browsers, via MSAL React).
   - Backend validates the JWT against the configured tenant's JWKS,
     enforces issuer / audience / expiry, and pins the tenant ID.
   - Authorisation: the token's `wids` claim must contain the **Global
     Administrator** directory role GUID
     (`62e90394-69f5-4237-9190-012177145e10`).
2. **`X-API-Key` header** (Apps Script, scripts/, ingest tooling).
   - Auto-generated on first boot, persisted to `<repo>/.api_key`.
   - Override with `DASHBOARD_API_KEY` in `.env`.

A request is authorised if **either** check passes. Public endpoints
(`/api/health`, `/api/auth/config`, `/api/_bootstrap`,
`/api/bridge-health`) bypass auth. The Chrome-extension ingest paths
keep their own `X-Ext-Key` flow.

### Why Global Administrator and not a custom App Role?

Per requirement: only Microsoft directory **Global Admins** of the
configured single tenant can use the dashboard. The check is on
`wids` (well-known directory role IDs) which Entra populates for any
token issued to the tenant — no extra app-role assignments required.

### Why the SPA + Bearer split?

Apps Script can't do an interactive Microsoft sign-in, so it stays on
the legacy API-key path. The browser portal is the only client that
goes through MSAL, and the `wids` admin check is the gate.

## Microsoft Entra setup (Azure CLI)

These steps register a single-tenant SPA app and configure access
tokens with the `wids` claim so the backend can read directory roles.

```bash
# 0. Install + sign in
az login
TENANT_ID=$(az account show --query tenantId -o tsv)
echo "Tenant: $TENANT_ID"

# 1. Create the SPA app registration
APP_NAME="BitCoding Outreach Dashboard"
APP_JSON=$(az ad app create --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --enable-id-token-issuance true \
  --enable-access-token-issuance true)
APP_ID=$(echo "$APP_JSON" | python -c "import json,sys; print(json.load(sys.stdin)['appId'])")
APP_OBJECT_ID=$(echo "$APP_JSON" | python -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "App (client) ID: $APP_ID"

# 2. SPA redirect URIs (dev + prod). Add the prod URL when you have it.
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body '{
    "spa": { "redirectUris": [
      "http://localhost:3000",
      "http://127.0.0.1:3000"
    ] }
  }'

# 3. Expose an API scope so the SPA can request a token *for our backend*
#    (instead of e.g. Microsoft Graph). The default identifierUri is
#    api://<app-id>; we'll keep that.
API_AUDIENCE="api://$APP_ID"
az ad app update --id $APP_ID --identifier-uris "$API_AUDIENCE"

USER_IMPERSONATION_SCOPE_ID=$(uuidgen)
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{
    \"api\": {
      \"oauth2PermissionScopes\": [{
        \"id\": \"$USER_IMPERSONATION_SCOPE_ID\",
        \"adminConsentDescription\": \"Allow the dashboard to call its own API on the user's behalf\",
        \"adminConsentDisplayName\": \"Access dashboard API\",
        \"isEnabled\": true,
        \"type\": \"User\",
        \"userConsentDescription\": \"Allow the dashboard to call its own API on your behalf\",
        \"userConsentDisplayName\": \"Access dashboard API\",
        \"value\": \"user_impersonation\"
      }],
      \"requestedAccessTokenVersion\": 2
    }
  }"

# 4. Pre-authorise the SPA itself for the scope we just exposed
#    (so users don't see a consent prompt for their own dashboard).
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{
    \"api\": {
      \"preAuthorizedApplications\": [{
        \"appId\": \"$APP_ID\",
        \"delegatedPermissionIds\": [\"$USER_IMPERSONATION_SCOPE_ID\"]
      }]
    }
  }"

# 5. CRITICAL — add `wids` as an optional claim on access tokens.
#    Without this, access tokens issued for our API don't carry
#    directory-role IDs, so the backend can't enforce the Global Admin
#    requirement.
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body '{
    "optionalClaims": {
      "accessToken": [
        { "name": "wids", "essential": false }
      ],
      "idToken": [
        { "name": "wids", "essential": false }
      ]
    }
  }'

# 6. Verify
az ad app show --id $APP_ID --query "{appId:appId,identifierUris:identifierUris,optionalClaims:optionalClaims}"
```

After step 5, populate `.env` with the values:

```
MS_TENANT_ID=<TENANT_ID>
MS_CLIENT_ID=<APP_ID>
MS_API_AUDIENCE=api://<APP_ID>
```

### Granting Global Administrator (one-off)

You probably already have one Global Admin in the tenant — that's the
person who can sign in. To grant the role to another user:

```bash
GA_ROLE_ID="62e90394-69f5-4237-9190-012177145e10"
USER_OBJ_ID=$(az ad user show --id someone@yourdomain.com --query id -o tsv)
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/directoryRoles/roleTemplateId=$GA_ROLE_ID/members/\$ref" \
  --headers "Content-Type=application/json" \
  --body "{\"@odata.id\": \"https://graph.microsoft.com/v1.0/directoryObjects/$USER_OBJ_ID\"}"
```

> Treat Global Administrator as the most privileged role in your
> tenant — assigning it should be a deliberate, audited action.

## .env.example

A complete `.env` template lives at `.env.example`. Copy and fill:

```bash
cp .env.example .env
```

## Testing the auth path

```bash
# 1. Health (public)
curl http://127.0.0.1:8000/api/health

# 2. Auth config (public, used by the SPA on boot)
curl http://127.0.0.1:8000/api/auth/config

# 3. With API key (service-to-service)
curl -H "X-API-Key: $(cat ../../.api_key)" http://127.0.0.1:8000/api/overview

# 4. With Microsoft access token (browser-acquired)
curl -H "Authorization: Bearer eyJ0eXAiOi..." http://127.0.0.1:8000/api/auth/me
```

## Frontend MSAL setup

Frontend reads `/api/auth/config` on boot — there are no MS-specific
environment variables on the frontend side. As long as the backend
publishes a non-empty `tenant_id` + `client_id`, the SPA will switch
into MSAL mode and gate every dashboard route behind `AdminGate`.

The redirect URI is the SPA's current origin (`window.location.origin`).
Make sure each origin you serve the dashboard from is registered in
the app registration's SPA redirect URIs (see step 2 above).
az ad app permission add  --id d7f0f9b0-4250-4765-b52d-dc571f61c5ca --api 00000003-0000-0000-c000-000000000046 --api-permissions 7ab1d382-f21e-4acd-a863-ba3e13f7da61=Role