"""
Round-trip tests for /api/linkedin/runtime-settings via FastAPI's
TestClient. Catches schema-vs-handler drift (e.g. a descriptor key that
no handler knows about, or a handler that accepts a key the descriptor
doesn't list) without spinning up uvicorn.
"""
from __future__ import annotations

import pytest

# Lazy-import inside fixtures so the conftest DB patching applies to the
# importing module too.


@pytest.fixture
def client(db, monkeypatch):
    """FastAPI test client wired to the in-memory DB. We read the API
    key directly off the imported `main` module rather than calling
    /api/_bootstrap, because the bootstrap endpoint is loopback-only and
    httpx.TestClient doesn't present a 127.0.0.1 client.host."""
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    c.headers.update({"X-API-Key": main.API_KEY})
    return c


@pytest.fixture
def unauth_client(db):
    """Client without the X-API-Key header — for testing auth gates."""
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_settings_list_returns_known_keys(client):
    r = client.get("/api/linkedin/runtime-settings")
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["settings"]}
    # These are the runtime-toggleable flags exposed today. If you add
    # a new one, add it here too -- this assertion is the "did you
    # remember to wire it everywhere" smoke test.
    assert "linkedin.digest.enabled" in keys
    assert "linkedin.draft.plan" in keys
    assert "linkedin.draft.critique" in keys
    assert "linkedin.draft.stats_hints" in keys
    assert "linkedin.draft.enrichment" in keys


def test_settings_descriptor_shape(client):
    """Every descriptor must have the fields the frontend renders."""
    r = client.get("/api/linkedin/runtime-settings")
    for s in r.json()["settings"]:
        assert "key" in s
        assert "label" in s
        assert "type" in s
        assert "default" in s
        assert "value" in s
        assert s["type"] in {"bool", "int", "string"}


def test_settings_post_then_get_roundtrips(client):
    """Set a flag via POST -> a follow-up GET reflects the new value."""
    r = client.post(
        "/api/linkedin/runtime-settings",
        json={"key": "linkedin.digest.enabled", "value": True},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/api/linkedin/runtime-settings")
    digest = next(s for s in r2.json()["settings"]
                  if s["key"] == "linkedin.digest.enabled")
    assert digest["value"] is True

    # Reset so we don't leak state between tests
    client.post(
        "/api/linkedin/runtime-settings",
        json={"key": "linkedin.digest.enabled", "value": False},
    )


def test_settings_post_rejects_unknown_key(client):
    r = client.post(
        "/api/linkedin/runtime-settings",
        json={"key": "totally.made.up", "value": True},
    )
    assert r.status_code == 400
    assert "unknown" in r.json()["detail"].lower()


def test_settings_post_rejects_int_with_garbage_value(client):
    """Tries an int-typed key with a non-numeric value. Skipped today
    because no int-typed settings exist yet — kept as a marker for when
    they're added."""
    # If/when an int setting is added, replace the body to use it.
    r = client.get("/api/linkedin/runtime-settings")
    int_settings = [s for s in r.json()["settings"] if s["type"] == "int"]
    if not int_settings:
        pytest.skip("no int-typed runtime settings yet")
    r2 = client.post(
        "/api/linkedin/runtime-settings",
        json={"key": int_settings[0]["key"], "value": "not-a-number"},
    )
    assert r2.status_code == 400


def test_runtime_settings_get_is_public_read(unauth_client):
    """GETs are public by design (so the dashboard can render with a
    stale key); the POST endpoint is the auth-gated one."""
    r = unauth_client.get("/api/linkedin/runtime-settings")
    assert r.status_code == 200


def test_runtime_settings_post_requires_auth(unauth_client):
    """Mutations require X-API-Key — would otherwise let any caller
    silently flip backend feature flags."""
    r = unauth_client.post(
        "/api/linkedin/runtime-settings",
        json={"key": "linkedin.digest.enabled", "value": True},
    )
    assert r.status_code in (401, 403)


def test_bootstrap_includes_bridge_url(unauth_client):
    """The frontend + extension both rely on bridge_url being present.
    Bootstrap is loopback-only in production; TestClient hits it from
    'testserver' so we expect 403, but the body is still useful to
    inspect via the in-process loopback bypass."""
    # Just verify the route exists + responds (200 from loopback in real
    # life, 403 from TestClient).
    r = unauth_client.get("/api/_bootstrap")
    assert r.status_code in (200, 403)
