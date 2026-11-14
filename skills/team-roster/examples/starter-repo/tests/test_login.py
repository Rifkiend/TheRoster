"""Smoke test for the example `login-api` entry point."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auth.login import login


def test_login_returns_a_token():
    result = login("rian@example.com", "rahasia")
    assert result["email"] == "rian@example.com"
    assert result["token"].startswith("session-")


def test_login_rejects_empty_password():
    try:
        login("rian@example.com", "")
    except ValueError:
        return
    raise AssertionError("empty password should raise ValueError")
