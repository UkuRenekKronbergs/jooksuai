"""Light/dark theme selector with refresh-persistent state.

Streamlit doesn't expose runtime theme switching, so dark mode is layered on
as a CSS override. The user's pick is persisted in ``st.session_state`` and
mirrored to the URL (``?theme=dark``) so a browser refresh keeps the theme.

The CSS targets a deliberately small set of selectors — broad surfaces
(``stApp``, sidebar, inputs, metrics, tabs, dataframe). Some less-common
widgets may look slightly off in dark mode; we accept that rather than
chasing every Streamlit-version-specific selector.
"""

from __future__ import annotations

import streamlit as st

_THEME_QUERY_KEY = "theme"
_THEME_STATE_KEY = "_vorm_theme"

_THEME_LIGHT = "light"
_THEME_DARK = "dark"

_THEME_LABELS = {
    _THEME_LIGHT: "🌞 Hele",
    _THEME_DARK: "🌙 Tume",
}
_VALID_THEMES = set(_THEME_LABELS)

_DARK_CSS = """
<style>
.stApp,
.stApp [data-testid="stHeader"],
.stApp [data-testid="stToolbar"] {
    background-color: #0e1117 !important;
    color: #fafafa !important;
}
.stApp section[data-testid="stSidebar"],
.stApp section[data-testid="stSidebar"] > div:first-child {
    background-color: #1a1d24 !important;
}
.stApp section[data-testid="stSidebar"] *,
.stApp [data-testid="stMarkdownContainer"] *,
.stApp [data-testid="stCaptionContainer"] * {
    color: #fafafa !important;
}
.stApp input,
.stApp textarea,
.stApp select,
.stApp [data-baseweb="select"] > div,
.stApp [data-baseweb="input"] > div {
    background-color: #262730 !important;
    color: #fafafa !important;
    border-color: #404552 !important;
}
.stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricLabel"],
.stApp [data-testid="stMetricDelta"] {
    color: #fafafa !important;
}
.stApp button[role="tab"]:not([aria-selected="true"]) {
    color: #c0c4cb !important;
}
.stApp [data-testid="stDataFrame"],
.stApp [data-testid="stTable"],
.stApp [data-testid="stExpander"],
.stApp [data-testid="stForm"] {
    background-color: #1a1d24 !important;
    border-color: #404552 !important;
}
/* Notification banners keep their accent color but text stays legible */
.stApp [data-testid="stNotification"] * {
    color: inherit !important;
}
</style>
"""


def _read_theme_from_url() -> str | None:
    try:
        raw = st.query_params.get(_THEME_QUERY_KEY)
    except Exception:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return None
    value = str(raw)
    return value if value in _VALID_THEMES else None


def _write_theme_to_url(theme: str) -> None:
    """Mirror the choice into the URL so a refresh keeps the theme.

    No-op if the URL already carries this value — avoids extra reruns when
    the selector renders without a change.
    """
    if _read_theme_from_url() == theme:
        return
    try:
        st.query_params[_THEME_QUERY_KEY] = theme
    except Exception:
        pass


def _current_theme() -> str:
    """Resolution order: session_state → URL → light (default)."""
    chosen = st.session_state.get(_THEME_STATE_KEY)
    if chosen in _VALID_THEMES:
        return chosen
    from_url = _read_theme_from_url()
    if from_url:
        st.session_state[_THEME_STATE_KEY] = from_url
        return from_url
    return _THEME_LIGHT


def apply_theme() -> None:
    """Inject CSS for the active theme. Call once near the top of the page."""
    if _current_theme() == _THEME_DARK:
        st.markdown(_DARK_CSS, unsafe_allow_html=True)


def render_theme_selector(*, container=None) -> None:
    """Render the theme picker. Pass ``container=st.sidebar`` to mount it there.

    Defaults to mounting on the main page so callers from the sidebar must
    pass it explicitly — keeps the API obvious about where the widget lands.
    """
    target = container if container is not None else st
    current = _current_theme()
    labels = list(_THEME_LABELS.values())
    ids = list(_THEME_LABELS.keys())

    selected_label = target.radio(
        "🎨 Teema",
        options=labels,
        index=ids.index(current),
        key="_vorm_theme_radio",
        horizontal=True,
    )
    selected_id = ids[labels.index(selected_label)]
    if selected_id != current:
        st.session_state[_THEME_STATE_KEY] = selected_id
        _write_theme_to_url(selected_id)
        st.rerun()
