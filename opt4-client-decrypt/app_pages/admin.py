import json
import platform

import duckdb
import pandas as pd
import streamlit as st

from secure_analytics.config import DEFAULT_SETTINGS, redact
from secure_analytics.crypto import DECRYPT_FAILED
from ui import state

cfg = state.config()
sess = state.session(cfg)
state.require("admin")

st.header("Admin", anchor=False)
view = st.segmented_control("Section", ["Security", "Settings", "Audit log", "Key test", "System"],
                            default="Security", key="admin_view", required=True)

if view == "Security":
    checks = [
        ("Sign-in", "OIDC" if cfg.oidc_configured else "dev sign-in only", cfg.oidc_configured),
        ("Dev sign-in disabled", "off" if not cfg.dev_login else "ON", not cfg.dev_login),
        ("AES-256 key", "loaded" if sess.decryptor else sess.key_error, sess.decryptor is not None),
        ("Plaintext storage", "in-memory DuckDB, per session", True),
        ("Cache file/network access", "disabled (enable_external_access=false, config locked)", True),
        ("Cache TTL", f"{cfg.settings['cache_ttl_minutes']} min, enforced on read", True),
        ("SQL", "single read-only statement (sqlglot)", True),
        ("CSV export", "enabled" if cfg.settings["csv_export_enabled"] else "disabled",
         not cfg.settings["csv_export_enabled"]),
        ("Decrypt failures this session", str(sum(e.decrypt_failures for e in sess.cache.entries.values())),
         all(e.decrypt_failures == 0 for e in sess.cache.entries.values())),
    ]
    st.dataframe(pd.DataFrame([{"": "✅" if ok else "⚠️", "control": c, "state": s} for c, s, ok in checks]),
                 hide_index=True, alt="Security checks")
    st.markdown("**Role mapping** (`[roles]` in secrets.toml)")
    if cfg.roles:
        st.dataframe(pd.DataFrame([{"role": r, "members": ", ".join(m)} for r, m in cfg.roles.items()]),
                     hide_index=True, alt="Role mapping")
    else:
        st.caption("No roles configured – every OIDC user gets `viewer`.")
    if st.button("Clear decrypted cache now", icon=":material/delete_sweep:"):
        sess.cache.clear()
        state.audit("cache_cleared")
        st.toast("Cache cleared", icon=":material/delete_sweep:")

elif view == "Settings":
    st.caption("Non-secret runtime settings, saved to `settings.yaml`. Credentials and keys stay in secrets.toml.")
    with st.form("settings"):
        s = dict(cfg.settings)
        c1, c2 = st.columns(2)
        s["cache_ttl_minutes"] = c1.number_input("Cache TTL (minutes)", 1, 24 * 60, int(s["cache_ttl_minutes"]))
        s["purge_interval_seconds"] = c2.number_input("Purge check interval (s)", 10, 3600, int(s["purge_interval_seconds"]))
        s["max_live_rows"] = c1.number_input("Max rows per live fetch", 100, 5_000_000, int(s["max_live_rows"]), step=1000)
        s["duckdb_memory_limit"] = c2.text_input("DuckDB memory limit", s["duckdb_memory_limit"])
        s["auto_decrypt_on_connect"] = c1.toggle("Decrypt automatically on connect", bool(s["auto_decrypt_on_connect"]))
        s["csv_export_enabled"] = c2.toggle("Allow CSV export of results", bool(s["csv_export_enabled"]))
        if st.form_submit_button("Save", icon=":material/save:", type="primary"):
            cfg.settings.update({k: s[k] for k in DEFAULT_SETTINGS})
            cfg.save_settings()
            state.audit("settings_saved", json.dumps({k: s[k] for k in DEFAULT_SETTINGS}))
            st.success("Saved. TTL and memory changes apply to new sessions.", icon=":material/check_circle:")
    with st.expander("Effective configuration (redacted)", icon=":material/data_object:"):
        try:
            st.json(redact(st.secrets.to_dict()))
        except Exception:
            st.caption("No secrets.toml found.")

elif view == "Audit log":
    log = pd.DataFrame(sess.audit[::-1])
    if log.empty:
        st.caption("No events yet.")
    else:
        ev = st.pills("Events", sorted(log.event.unique()), selection_mode="multi", key="audit_filter")
        st.dataframe(log[log.event.isin(ev)] if ev else log, hide_index=True, alt="Audit log")
        st.download_button("Download log", log.to_csv(index=False), "audit.csv", "text/csv",
                           icon=":material/download:", on_click="ignore")

elif view == "Key test":
    if sess.decryptor is None:
        st.error(sess.key_error, icon=":material/key_off:")
    else:
        st.caption("Round-trip a value with the configured key, or decrypt a ciphertext copied from a live query.")
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            v = st.text_input("Plaintext", "Hello sovereignty")
            if v:
                ct = sess.decryptor.encrypt(v)
                st.code(ct, language=None, wrap_lines=True)
                ok = sess.decryptor.decrypt(ct) == v
                st.badge("round-trip ok" if ok else "round-trip failed", color="green" if ok else "red")
        with c2.container(border=True):
            ct = st.text_area("Ciphertext", height=110)
            if ct:
                pt = sess.decryptor.decrypt(ct)
                (st.error if pt == DECRYPT_FAILED else st.success)(pt)

else:
    import streamlit, cryptography, sqlglot
    info = {"python": platform.python_version(), "platform": platform.platform(), "streamlit": streamlit.__version__,
            "duckdb": duckdb.__version__, "cryptography": cryptography.__version__, "sqlglot": sqlglot.__version__}
    st.dataframe(pd.DataFrame(info.items(), columns=["component", "version"]), hide_index=True, alt="Versions")
    st.markdown("**Cached tables**")
    e = sess.cache.live_entries()
    st.dataframe(pd.DataFrame([{"table": x.name, "source": x.source, "rows": x.rows,
                                "decrypted": ", ".join(x.decrypted_columns), "failures": x.decrypt_failures,
                                "expires": pd.Timestamp(x.expires_at, unit="s", tz="UTC").tz_convert(None)}
                               for x in e.values()]), hide_index=True, alt="Cached tables")
    st.markdown("**Connections**")
    st.dataframe(pd.DataFrame([{"name": n, "kind": s.kind, **s.info} for n, s in sess.sources.items()]),
                 hide_index=True, alt="Connections")
