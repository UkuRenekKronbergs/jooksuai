from __future__ import annotations

import time
from threading import RLock
from types import SimpleNamespace

from vorm import auth


def _registry():
    return {"lock": RLock(), "sessions": {}}


def test_browser_session_key_uses_vorm_cookie(monkeypatch):
    cookie_value = "vorm-session-cookie-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(auth, "_read_browser_session_cookie", lambda: cookie_value)
    monkeypatch.setattr(auth.st, "session_state", {})

    assert auth._browser_session_key() == cookie_value
    assert auth.st.session_state[auth._COOKIE_SESSION_KEY] == cookie_value


def test_sync_browser_session_cookie_creates_opaque_cookie(monkeypatch):
    rendered = []
    generated = "generated-session-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(auth, "_read_browser_session_cookie", lambda: None)
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda length: generated)
    monkeypatch.setattr(
        auth,
        "_render_cookie_script",
        lambda session_id=None, *, delete=False: rendered.append((session_id, delete)),
    )
    monkeypatch.setattr(auth.st, "session_state", {})

    auth._sync_browser_session_cookie()

    assert auth.st.session_state[auth._COOKIE_SESSION_KEY] == generated
    assert rendered == [(generated, False)]


def test_forget_persistent_session_schedules_cookie_delete(monkeypatch):
    registry = _registry()
    registry["sessions"]["browser-1"] = auth._PersistedSession(
        kind="guest",
        user=None,
        updated_at=time.time(),
    )
    monkeypatch.setattr(auth, "_browser_session_key", lambda: "browser-1")
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(auth.st, "session_state", {})

    auth._forget_persistent_session()

    assert registry["sessions"] == {}
    assert auth.st.session_state[auth._COOKIE_DELETE_PENDING_KEY] is True


def test_restore_persistent_guest(monkeypatch):
    registry = _registry()
    monkeypatch.setattr(auth, "_browser_session_key", lambda: "browser-1")
    monkeypatch.setattr(auth, "_render_cookie_script", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(auth.st, "session_state", {})

    auth._remember_persistent_session("guest")

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
    registry["sessions"]["browser-1"] = auth._PersistedSession(
        kind="user",
        user=old_user,
        updated_at=time.time(),
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
    monkeypatch.setattr(auth, "_browser_session_key", lambda: "browser-1")
    monkeypatch.setattr(auth, "_render_cookie_script", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth, "_persistent_sessions", lambda: registry)
    monkeypatch.setattr(auth, "_get_client", lambda: fake_client)
    monkeypatch.setattr(auth.st, "session_state", {})

    restored = auth._restore_persistent_session()

    assert restored == auth.AuthUser(
        id="user-1",
        email="runner@example.com",
        access_token="new-access",
        refresh_token="new-refresh",
    )
    assert auth.current_user() == restored
    assert registry["sessions"]["browser-1"].user == restored


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
    monkeypatch.setattr(auth, "_forget_persistent_session", lambda: forgotten.append(True))
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
