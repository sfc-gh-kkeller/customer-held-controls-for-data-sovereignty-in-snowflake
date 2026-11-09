"""Authentication & role resolution.

Production: OIDC via ``st.login`` (configure ``[auth]`` in secrets.toml).
Demo:       ``[app].dev_login = true`` enables a clearly bannered local sign-in – never enable it in production.
Roles come from ``[roles]`` in secrets: ``admin = ["alice@corp.com", "@corp.com"]``. Users without a match get ``viewer``.
"""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from .config import AppConfig

ROLE_ORDER = ["viewer", "analyst", "admin"]
PERMISSIONS = {
    "viewer": {"query_cache"},
    "analyst": {"query_cache", "query_live", "connect", "compare"},
    "admin": {"query_cache", "query_live", "connect", "compare", "admin"},
}


@dataclass(frozen=True)
class Identity:
    email: str
    name: str
    role: str
    method: str  # "oidc" | "dev"

    def can(self, perm: str) -> bool:
        return perm in PERMISSIONS.get(self.role, set())


def resolve_role(email: str, roles: dict[str, list[str]]) -> str:
    email = email.lower()
    domain = "@" + email.split("@")[-1] if "@" in email else None
    best = "viewer"
    for role, members in roles.items():
        if role in ROLE_ORDER and (email in members or (domain and domain in members)):
            if ROLE_ORDER.index(role) > ROLE_ORDER.index(best):
                best = role
    return best


def current_identity(cfg: AppConfig) -> Identity | None:
    if cfg.oidc_configured and st.user.is_logged_in:
        email = str(st.user.get("email", "") or "")
        return Identity(email, str(st.user.get("name", email)), resolve_role(email, cfg.roles), "oidc")
    if cfg.dev_login:
        return st.session_state.get("dev_identity")
    return None


def sign_out(identity: Identity) -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    if identity.method == "oidc":
        st.logout()
    st.rerun()


def render_sign_in(cfg: AppConfig) -> None:
    """Sign-in screen; call st.stop() after it."""
    with st.container(border=True, width=480):
        st.subheader(":material/lock: Secure analytics")
        st.caption("Client-side decryption of Snowflake ciphertext. Keys and plaintext never leave this host.")
        if cfg.oidc_configured:
            st.button("Sign in with SSO", icon=":material/login:", type="primary", width="stretch", on_click=st.login)
        if cfg.dev_login:
            st.error("Development sign-in is enabled. Disable `[app].dev_login` outside of demos.",
                     icon=":material/warning:")
            with st.form("dev_login"):
                email = st.text_input("Email", value="demo@localhost")
                role = st.segmented_control("Role", ROLE_ORDER, default="admin")
                if st.form_submit_button("Continue", width="stretch") and email and role:
                    st.session_state.dev_identity = Identity(email, email.split("@")[0], role, "dev")
                    st.rerun()
        if not cfg.oidc_configured and not cfg.dev_login:
            st.warning("No sign-in method configured. Add an `[auth]` OIDC provider to `.streamlit/secrets.toml`.",
                       icon=":material/key_off:")
