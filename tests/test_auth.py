"""Tests for vorm.auth — URL-query-param session persistence.

The cookie/JS-bridge implementation was retired because it didn't survive a
tab refresh on Streamlit Cloud. The new mechanism uses a server-side registry
keyed by an opaque token attached as ``?s=<token>``; ``st.query_params``
reads/writes that token synchronously, so refresh works without any iframe
sandbox dance.
"""

from __future__ import annotations

import time
from threading import RLock
from types import SimpleNamespace

from vorm import auth

_VALID_TOKEN = "abcdefghijklmnopqrstuvwxyz1234"  # 30 chars, alnum — valid id


def _registry():
    return {"lock": RLock(), "sessions": {}}


# --- URL session-token plumbing ----------------------------------------

def test_read_session_token_rejects_short_or_dirty_tokens(monkeypatch):
    """`?s=` values shorter than 24 chars or with disallowed chars are ignored.

    A stray short token shouldn't crash auth, just degrade to no-session.
    """
    monkeypatch.setattr(auth.st, "query_params", {"s": "too-short"})
    assert auth._read_session_token() is None

    monkeypatch.setattr(auth.st, "query_params", {"s": "bad chars in url!!!"})
    assert auth._read_session_token() is None


def test_read_session_token_returns_valid_token(monkeypatch):
    monkeypatch.setattr(auth.st, "query_params", {"s": _VALID_TOKEN})
    assert auth._read_session_token() == _VALID_TOKEN


def test_write_session_token_skips_when_unchanged(monkeypatch):
    """Writing the same token shouldn't mutate query_params (avoids rerun spam)."""

    class CountingDict(dict):
        writes = 0

        def __setitem__(self, key, value):
            CountingDict.writes += 1
            super().__setitem__(key, value)

    qp = CountingDict({"s": _VALID_TOKEN})
    monkeypatch.setattr(auth.st, "query_params", qp)

    auth._write_session_token(_VALID_TOKEN)
    assert CountingDict.writes == 0


def test_clear_session_token_only_drops_session_key(monkeypatch):
    qp = {"s": _VALID_TOKEN, "other": "x"}
    monkeypatch.setattr(auth.st, "query_params", qp)
    auth._clear_session_token()
    assert qp == {"other": "x"}


# --- Server-side registry ----------------------------------------------

def test_remember_mints_token_and_registers_session(monkeypatch):
    registry = _registry()
    qp: dict = {}
    monkeypatch.setattr(auth.st, "query_params", qp)
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _n: _VALID_TOKEN)

    auth._remember_persistent_session("guest")

    assert qp["s"] == _VALID_TOKEN
    persisted = registry["sessions"][_VALID_TOKEN]
    assert persisted.kind == "guest"
    assert persisted.user is None


def test_remember_reuses_existing_url_token(monkeypatch):
    """When the URL already carries a token, don't mint a new one — keeps
    navigation stable for a logged-in user opening multiple tabs."""
    registry = _registry()
    qp = {"s": _VALID_TOKEN}
    monkeypatch.setattr(auth.st, "query_params", qp)
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(
        auth.secrets, "token_urlsafe", lambda _n: "should-not-be-used",
    )

    auth._remember_persistent_session("guest")

    assert qp["s"] == _VALID_TOKEN
    assert _VALID_TOKEN in registry["sessions"]


def test_forget_removes_from_registry_and_url(monkeypatch):
    registry = _registry()
    registry["sessions"][_VALID_TOKEN] = auth._PersistedSession(
        kind="user", user=None, updated_at=time.time(),
    )
    qp = {"s": _VALID_TOKEN}
    monkeypatch.setattr(auth.st, "query_params", qp)
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)

    auth._forget_persistent_session()

    assert registry["sessions"] == {}
    assert "s" not in qp


def test_load_persistent_session_bumps_ttl(monkeypatch):
    """Loading a session should refresh its updated_at so an actively-used
    session doesn't get pruned."""
    registry = _registry()
    stale_time = time.time() - 60 * 60  # 1 hour ago
    registry["sessions"][_VALID_TOKEN] = auth._PersistedSession(
        kind="user", user=None, updated_at=stale_time,
    )
    monkeypatch.setattr(auth.st, "query_params", {"s": _VALID_TOKEN})
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)

    auth._load_persistent_session()

    assert registry["sessions"][_VALID_TOKEN].updated_at > stale_time


# --- High-level restore flow ------------------------------------------

def test_restore_persistent_guest(monkeypatch):
    registry = _registry()
    registry["sessions"][_VALID_TOKEN] = auth._PersistedSession(
        kind="guest", user=None, updated_at=time.time(),
    )
    monkeypatch.setattr(auth.st, "query_params", {"s": _VALID_TOKEN})
    monkeypatch.setattr(auth.st, "session_state", {})
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)

    assert auth._restore_persistent_session() is None
    assert auth.is_guest()


def test_restore_persistent_user_refreshes_supabase_session(monkeypatch):
    registry = _registry()
    old_user = auth.AuthUser(
        id="user-1",
        email="runner@example.com",
        access_token="old-access",
        refresh_token="old-refresh",
    )
    registry["sessions"][_VALID_TOKEN] = auth._PersistedSession(
        kind="user", user=old_user, updated_at=time.time(),
    )

    class FakeAuth:
        def refresh_session(self, refresh_token):
            assert refresh_token == "old-refresh"
            return SimpleNamespace(
                session=SimpleNamespace(
                    access_token="new-access",
                    refresh_token="new-refresh",
                ),
                user=SimpleNamespace(id="user-1", email="runner@example.com"),
            )

    fake_client = SimpleNamespace(auth=FakeAuth())
    monkeypatch.setattr(auth.st, "query_params", {"s": _VALID_TOKEN})
    monkeypatch.setattr(auth.st, "session_state", {})
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(auth, "_get_client", lambda: fake_client)

    restored = auth._restore_persistent_session()

    expected = auth.AuthUser(
        id="user-1",
        email="runner@example.com",
        access_token="new-access",
        refresh_token="new-refresh",
    )
    assert restored == expected
    assert auth.current_user() == expected
    assert registry["sessions"][_VALID_TOKEN].user == expected


# --- Password reset flow (backend unchanged by the URL-token rewrite) ---

def test_request_password_reset_uses_current_app_redirect(monkeypatch):
    calls = []

    class FakeAuth:
        def reset_password_for_email(self, email, options=None):
            calls.append((email, options))

    monkeypatch.setattr(
        auth, "_get_client", lambda: SimpleNamespace(auth=FakeAuth())
    )
    monkeypatch.setattr(
        auth,
        "_password_reset_redirect_url",
        lambda: "http://localhost:8501/?auth_flow=password_reset",
    )

    auth.request_password_reset("runner@example.com")

    assert calls == [
        (
            "runner@example.com",
            {"redirect_to": "http://localhost:8501/?auth_flow=password_reset"},
        )
    ]


def test_consume_password_recovery_code_sets_recovery_mode(monkeypatch):
    remembered = []
    forgotten = []

    class FakeQueryParams(dict):
        pass

    class FakeAuth:
        def exchange_code_for_session(self, params):
            assert params == {"auth_code": "recover-code"}
            return SimpleNamespace(
                session=SimpleNamespace(
                    access_token="recovered-access",
                    refresh_token="recovered-refresh",
                ),
                user=SimpleNamespace(id="user-1", email="runner@example.com"),
            )

    query_params = FakeQueryParams(
        {"auth_flow": "password_reset", "code": "recover-code"}
    )
    monkeypatch.setattr(
        auth, "_get_client", lambda: SimpleNamespace(auth=FakeAuth())
    )
    monkeypatch.setattr(
        auth,
        "_remember_persistent_session",
        lambda kind, user=None: remembered.append((kind, user)),
    )
    monkeypatch.setattr(
        auth, "_forget_persistent_session", lambda: forgotten.append(True),
    )
    monkeypatch.setattr(auth.st, "query_params", query_params)
    monkeypatch.setattr(auth.st, "session_state", {})

    recovered = auth._consume_password_recovery_from_url()

    assert recovered == auth.AuthUser(
        id="user-1",
        email="runner@example.com",
        access_token="recovered-access",
        refresh_token="recovered-refresh",
    )
    assert auth.current_user() == recovered
    assert auth.st.session_state[auth._PASSWORD_RECOVERY_KEY] is True
    assert remembered == []
    assert forgotten == [True]
    assert query_params == {}


def test_update_password_calls_supabase_and_keeps_latest_session(monkeypatch):
    calls = []
    remembered = []
    auth.st.session_state = {
        auth._USER_KEY: auth.AuthUser(
            id="user-1",
            email="runner@example.com",
            access_token="old-access",
            refresh_token="old-refresh",
        )
    }

    class FakeAuth:
        def update_user(self, attributes):
            calls.append(attributes)

        def get_session(self):
            return SimpleNamespace(
                access_token="new-access",
                refresh_token="new-refresh",
                user=SimpleNamespace(id="user-1", email="runner@example.com"),
            )

    monkeypatch.setattr(
        auth, "_get_client", lambda: SimpleNamespace(auth=FakeAuth())
    )
    monkeypatch.setattr(auth, "_bind_session", lambda client, user: None)
    monkeypatch.setattr(
        auth,
        "_remember_persistent_session",
        lambda kind, user=None: remembered.append((kind, user)),
    )

    updated = auth.update_password("new-password")

    assert calls == [{"password": "new-password"}]
    assert updated == auth.AuthUser(
        id="user-1",
        email="runner@example.com",
        access_token="new-access",
        refresh_token="new-refresh",
    )
    assert auth.current_user() == updated
    assert remembered == [("user", updated)]
