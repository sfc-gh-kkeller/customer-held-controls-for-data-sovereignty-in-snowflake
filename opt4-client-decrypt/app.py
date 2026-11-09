"""Secure Analytics – entry point.  Run: pixi run app"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from secure_analytics import auth  # noqa: E402
from ui import state  # noqa: E402

st.set_page_config(page_title="Secure analytics", page_icon=":material/encrypted:", layout="wide")

cfg = state.config()
ident = auth.current_identity(cfg)
if ident is None:
    auth.render_sign_in(cfg)
    st.stop()
st.session_state.identity = ident
sess = state.session(cfg)

pages = [
    st.Page("app_pages/connect.py", title="Connect", icon=":material/cable:", default=True),
    st.Page("app_pages/query.py", title="Query", icon=":material/terminal:"),
    st.Page("app_pages/explore.py", title="Explore", icon=":material/insights:"),
]
if ident.can("compare"):
    pages.append(st.Page("app_pages/compare.py", title="Compare", icon=":material/compare_arrows:"))
if ident.can("admin"):
    pages.append(st.Page("app_pages/admin.py", title="Admin", icon=":material/admin_panel_settings:"))
page = st.navigation(pages, position="top")

# ---- sidebar: identity + live status ----------------------------------------
with st.sidebar:
    st.markdown("### :material/encrypted: Secure analytics")
    st.caption("Decryption happens on this host only.")
    with st.container(border=True):
        st.markdown(f"**{ident.name}**  \n{ident.email}")
        st.badge(ident.role, icon=":material/badge:", color="violet")
        if ident.method == "dev":
            st.badge("dev sign-in", icon=":material/warning:", color="red")
    src = sess.primary
    stats = sess.cache.stats()
    st.markdown("**Status**")
    if src:
        st.badge(f"{src.kind}: {src.info.get('account') or src.info.get('host')}", icon=":material/link:",
                 color="green")
    else:
        st.badge("not connected", icon=":material/link_off:", color="gray")
    if sess.key_error:
        st.badge("no AES key", icon=":material/key_off:", color="red")
    else:
        st.badge("AES-256 key loaded", icon=":material/key:", color="green")
    st.badge(f"{stats['tables']} tables · {stats['rows']:,} rows cached", icon=":material/database:", color="blue")
    if stats["next_expiry"]:
        import time
        mins = max(0, int((stats["next_expiry"] - time.time()) / 60))
        st.caption(f"Next cache expiry in {mins} min (TTL {cfg.settings['cache_ttl_minutes']} min).")
    st.space("small")
    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        for s in sess.sources.values():
            s.close()
        sess.cache.clear()
        auth.sign_out(ident)

if ident.method == "dev":
    st.warning("Development sign-in active – identities are not verified.", icon=":material/warning:")

page.run()
