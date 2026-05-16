"""Streamlit auth helpers backed by Supabase Auth (email + password).

Flow:
1. **Sign up** → user enters email + password. Supabase sends a confirm-email
   link (requires *Confirm email* ON in Supabase Auth settings — the default).
   User clicks the link once → account is confirmed.
2. **Sign in** → user enters email + password. Returns a Session that we cache
   in ``st.session_state``.

The session lives as long as Streamlit keeps the tab's session_state. Browser
refresh = re-login. For persistence across refreshes we'd add cookie storage
(e.g. ``streamlit-extras``), which is out of scope for the validation phase.

Password reset / forgot-password isn't wired into the UI yet — recover via
the Supabase Dashboard (Authentication → Users → ⋯ → Send password recovery)
if a user gets locked out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import streamlit as st

from .config import load_config
from .data.supabase_store import SupabaseNotConfigured, SupabaseStore

if TYPE_CHECKING:
    from supabase import Client


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    access_token: str
    refresh_token: str


_CLIENT_KEY = "_vorm_supabase_client"
_USER_KEY = "_vorm_auth_user"
_PENDING_CONFIRM_KEY = "_vorm_auth_pending_confirm_email"
_GUEST_KEY = "_vorm_guest_mode"


def _build_client() -> Client:
    cfg = load_config()
    if not (cfg.supabase_url and cfg.supabase_anon_key):
        raise SupabaseNotConfigured(
            "Supabase pole seadistatud (SUPABASE_URL + SUPABASE_ANON_KEY puudu)."
        )
    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError as exc:
        raise SupabaseNotConfigured(
            "Supabase Python klient pole installitud. Lisa requirements.txt-i 'supabase>=2.0' "
            "ja jooksuta `pip install -r requirements.txt`."
        ) from exc
    return create_client(cfg.supabase_url, cfg.supabase_anon_key)


def _get_client() -> Client:
    """One client per Streamlit user session, cached in session_state.

    We don't use ``@st.cache_resource`` because that shares across all users;
    the supabase-py client holds auth state internally and sharing it would
    leak sessions between users.
    """
    client = st.session_state.get(_CLIENT_KEY)
    if client is None:
        client = _build_client()
        st.session_state[_CLIENT_KEY] = client
    return client


def current_user() -> AuthUser | None:
    return st.session_state.get(_USER_KEY)


def is_authenticated() -> bool:
    return current_user() is not None


def is_guest() -> bool:
    """True when the user picked 'continue as guest' on the login gate.

    Guests bypass the login but their data goes to local SQLite — same code
    path as anonymous mode (no Supabase configured at all). Nothing they
    enter touches the cloud, so reruns within the session persist but
    deploys / hard-refreshes lose state.
    """
    return bool(st.session_state.get(_GUEST_KEY))


def enter_guest_mode() -> None:
    st.session_state[_GUEST_KEY] = True


def exit_guest_mode() -> None:
    st.session_state.pop(_GUEST_KEY, None)


def _bind_session(client: Client, user: AuthUser) -> None:
    """Restore the user's JWT on the client so PostgREST requests pass RLS.

    The client persists auth state in memory by default, but Streamlit may
    reconstruct the client on a rerun if session_state was cleared — so we
    re-bind defensively. Catches errors silently because the next API call
    will raise the real error if the session is genuinely invalid.
    """
    try:
        client.auth.set_session(user.access_token, user.refresh_token)
    except Exception:
        pass


def sign_up(email: str, password: str) -> bool:
    """Create a new account. Supabase sends a confirm-email link.

    Does NOT sign the user in — they must click the confirmation link first.
    Returns True when the signup call succeeded (regardless of whether email
    confirmation is required). Raises on actual failures (existing user,
    weak password, network errors).
    """
    client = _get_client()
    client.auth.sign_up({"email": email, "password": password})
    return True


def sign_in(email: str, password: str) -> AuthUser:
    """Sign in with existing credentials. Stashes the session in state."""
    client = _get_client()
    resp = client.auth.sign_in_with_password(
        {"email": email, "password": password}
    )
    if not resp.session or not resp.user:
        raise RuntimeError("Sisselogimine ebaõnnestus.")
    user = AuthUser(
        id=resp.user.id,
        email=resp.user.email or email,
        access_token=resp.session.access_token,
        refresh_token=resp.session.refresh_token,
    )
    st.session_state[_USER_KEY] = user
    return user


def sign_out() -> None:
    client = st.session_state.get(_CLIENT_KEY)
    if client is not None:
        try:
            client.auth.sign_out()
        except Exception:
            # Local-only sign-out is fine even if the remote call fails (e.g.
            # offline) — clearing session_state below is what matters here.
            pass
    for key in (_USER_KEY, _PENDING_CONFIRM_KEY, _CLIENT_KEY):
        st.session_state.pop(key, None)


def get_store() -> SupabaseStore | None:
    """SupabaseStore for the signed-in user, or None if not authenticated.

    Re-binds the session on every call so RLS-protected reads/writes succeed
    even after a Streamlit script rerun.
    """
    user = current_user()
    if not user:
        return None
    client = _get_client()
    _bind_session(client, user)
    return SupabaseStore(client=client, user_id=user.id)


def _humanize_auth_error(exc: Exception) -> str:
    """Translate Supabase auth errors into Estonian one-liners.

    Supabase returns these as ``AuthApiError`` with a ``message`` attribute;
    we match on substrings because the structured error codes differ across
    supabase-py versions.
    """
    msg = str(exc).lower()
    if "invalid login credentials" in msg or "invalid_credentials" in msg:
        return "Vale email või parool."
    if "email not confirmed" in msg or "email_not_confirmed" in msg:
        return (
            "Email pole veel kinnitatud. Kontrolli postkasti — saatsime "
            "esmasel registreerumisel kinnitusmaili. Klikka seal lingile "
            "ja proovi seejärel uuesti."
        )
    if "user already registered" in msg or "already_exists" in msg:
        return "See email on juba registreeritud. Logi sisse 'Logi sisse' tabist."
    if "password" in msg and ("short" in msg or "weak" in msg or "6 char" in msg):
        return "Parool on liiga lühike — peab olema vähemalt 6 märki."
    if "rate limit" in msg or "429" in msg:
        return "Liiga palju katseid. Oota minut ja proovi uuesti."
    return f"Tõrge: {exc}"


def render_login_gate() -> AuthUser | None:
    """Block the app behind an email/password login form. Returns the user or None.

    Call near the top of ``app.py``. If the return is None **and**
    ``is_guest()`` is False, the caller should ``st.stop()`` — nothing else
    should render until the user either signs in or picks guest mode.
    """
    user = current_user()
    if user:
        return user
    # Guests have already bypassed the gate; don't re-render the form.
    if is_guest():
        return None

    st.title("🏃 Vorm.ai")
    st.markdown("### 🔐 Logi sisse, et jätkata")

    # If we just sent a confirm-email, surface that prominently above the tabs
    # so the user knows what to do next.
    pending = st.session_state.get(_PENDING_CONFIRM_KEY)
    if pending:
        st.success(
            f"✉️ Konto loodud. Saatsime kinnitusmaili aadressile **{pending}**. "
            "Klikka seal lingile, et email kinnitada — seejärel saad allpool sisse logida."
        )

    login_tab, signup_tab = st.tabs(["Logi sisse", "Loo uus konto"])

    with login_tab:
        with st.form("vorm_login_form"):
            email = st.text_input(
                "Email", placeholder="sinu@email.ee", key="_vorm_login_email",
            )
            password = st.text_input(
                "Parool", type="password", key="_vorm_login_password",
            )
            submitted = st.form_submit_button(
                "Logi sisse", type="primary", use_container_width=True
            )
            if submitted:
                cleaned_email = (email or "").strip().lower()
                if not cleaned_email or not password:
                    st.error("Email ja parool on kohustuslikud.")
                    return None
                try:
                    sign_in(cleaned_email, password)
                except SupabaseNotConfigured as exc:
                    st.error(str(exc))
                    return None
                except Exception as exc:
                    st.error(_humanize_auth_error(exc))
                    return None
                st.session_state.pop(_PENDING_CONFIRM_KEY, None)
                st.rerun()

    with signup_tab:
        with st.form("vorm_signup_form"):
            new_email = st.text_input(
                "Email", placeholder="sinu@email.ee", key="_vorm_signup_email",
            )
            new_password = st.text_input(
                "Parool",
                type="password",
                key="_vorm_signup_password",
                help="Vähemalt 6 märki.",
            )
            new_password_confirm = st.text_input(
                "Korda parooli",
                type="password",
                key="_vorm_signup_password_confirm",
            )
            submitted = st.form_submit_button(
                "Loo konto", type="primary", use_container_width=True
            )
            if submitted:
                cleaned_email = (new_email or "").strip().lower()
                if not cleaned_email or not new_password:
                    st.error("Email ja parool on kohustuslikud.")
                    return None
                if "@" not in cleaned_email or "." not in cleaned_email.split("@")[-1]:
                    st.error("Vigane email-aadress.")
                    return None
                if new_password != new_password_confirm:
                    st.error("Paroolid ei klapi.")
                    return None
                if len(new_password) < 6:
                    st.error("Parool peab olema vähemalt 6 märki.")
                    return None
                try:
                    sign_up(cleaned_email, new_password)
                except SupabaseNotConfigured as exc:
                    st.error(str(exc))
                    return None
                except Exception as exc:
                    st.error(_humanize_auth_error(exc))
                    return None
                st.session_state[_PENDING_CONFIRM_KEY] = cleaned_email
                st.rerun()

    # Guest-mode escape hatch — useful for course-project demos and reviewers
    # who don't want to create an account just to poke at the UI.
    st.divider()
    st.caption(
        "Soovid lihtsalt rakendust katsetada ilma kontot loomata? "
        "Külalisrežiimis sinu andmed **ei salvestu pilve** — kõik on "
        "ainult selle brauserisessiooni jaoks."
    )
    if st.button(
        "👤 Jätka külalisena",
        key="_vorm_enter_guest",
        use_container_width=True,
    ):
        enter_guest_mode()
        st.rerun()

    return None


def render_sidebar_user_panel() -> None:
    """Show the signed-in user or guest banner + sign-out button in the sidebar."""
    user = current_user()
    if user:
        with st.sidebar:
            st.markdown(f"👤 **{user.email}**")
            if st.button("Logi välja", key="_vorm_sign_out", use_container_width=True):
                sign_out()
                st.rerun()
        return
    if is_guest():
        with st.sidebar:
            st.markdown("👤 **Külaline**")
            st.caption("Andmed ei salvestu pilve.")
            if st.button(
                "Logi sisse / loo konto",
                key="_vorm_exit_guest",
                use_container_width=True,
            ):
                exit_guest_mode()
                st.rerun()
