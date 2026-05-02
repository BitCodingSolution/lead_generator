"""
Tests for the impostor-resistant bridge health probe. The probe must
return False when a foreign service squats the bridge port (real bug
that bit us once when a Next.js dev server grabbed :8765).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import requests

import linkedin_claude


def _mock_response(status: int, json_body=None, raise_for_json=False):
    r = MagicMock()
    r.status_code = status
    if raise_for_json:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = json_body or {}
    return r


def test_bridge_up_when_real_bridge_responds():
    """The real bridge returns service: 'LinkedIn Smart Search ...'."""
    real = _mock_response(200, {"ok": True, "service": "LinkedIn Smart Search Bridge", "version": "0.1.0"})
    with patch.object(requests, "get", return_value=real):
        assert linkedin_claude.bridge_is_up() is True


def test_bridge_down_when_squatter_returns_200_html():
    """A foreign HTTP server (e.g. another Next.js project on the port)
    returns 200 with no service signature -> must NOT count as up."""
    foreign = _mock_response(200, raise_for_json=True)
    with patch.object(requests, "get", return_value=foreign):
        assert linkedin_claude.bridge_is_up() is False


def test_bridge_down_when_signature_mismatch():
    """Some other JSON service on the port -> json parses but has wrong service name."""
    foreign = _mock_response(200, {"service": "Some Other API", "version": "9"})
    with patch.object(requests, "get", return_value=foreign):
        assert linkedin_claude.bridge_is_up() is False


def test_bridge_down_when_service_field_missing():
    """JSON response without a 'service' key -> not our bridge."""
    foreign = _mock_response(200, {"ok": True})
    with patch.object(requests, "get", return_value=foreign):
        assert linkedin_claude.bridge_is_up() is False


def test_bridge_down_on_non_200():
    not_found = _mock_response(404, {"detail": "Not Found"})
    with patch.object(requests, "get", return_value=not_found):
        assert linkedin_claude.bridge_is_up() is False


def test_bridge_down_on_connection_refused():
    with patch.object(requests, "get",
                       side_effect=requests.exceptions.ConnectionError()):
        assert linkedin_claude.bridge_is_up() is False


def test_bridge_down_on_timeout():
    with patch.object(requests, "get",
                       side_effect=requests.exceptions.Timeout()):
        assert linkedin_claude.bridge_is_up() is False
