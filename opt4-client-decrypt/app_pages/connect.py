import pandas as pd
import streamlit as st

from secure_analytics import decrypt_pipeline
from secure_analytics.config import EncryptedTable
from secure_analytics.connections import ConnectError, connect_postgres, connect_snowflake
from ui import state

cfg = state.config()
sess = state.session(cfg)
ident = state.identity()

st.header("Connect", anchor=False)
st.caption("Pull ciphertext from Snowflake, decrypt it here, and keep plaintext in an in-memory cache.")


def _load(source):
    if sess.decryptor is None:
        st.error(sess.key_error, icon=":material/key_off:")
        return
    tables = st.session_state.enc_tables
    if not tables:
        st.info("No encrypted tables configured – add one below.", icon=":material/info:")
        return
    with st.status("Decrypting tables locally…", expanded=False) as status:
        reports = decrypt_pipeline.load_all(source, tables, sess.decryptor, sess.cache,
                                            int(cfg.settings["max_live_rows"]))
        for r in reports:
            state.audit("decrypt_load" if r.ok else "decrypt_error",
                        f"{r.table}: {r.rows} rows, cols={r.decrypted_columns}, failures={r.failures} {r.error}")
        bad = [r for r in reports if not r.ok or r.failures]
        status.update(label=f"Loaded {sum(r.ok for r in reports)}/{len(reports)} tables",
                      state="error" if bad else "complete")
    st.session_state.load_reports = reports


if "enc_tables" not in st.session_state:
    st.session_state.enc_tables = list(cfg.encrypted_tables)

# ---- connection --------------------------------------------------------------
if sess.sources:
    for name, src in list(sess.sources.items()):
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(f":material/check_circle: **{name}** · {src.kind}")
            c1.caption(" · ".join(f"{k}: {v}" for k, v in src.info.items() if v))
            if c2.button("Disconnect", key=f"dc_{name}", icon=":material/link_off:", width="stretch"):
                src.close()
                del sess.sources[name]
                state.audit("disconnect", name)
                st.rerun()

if not ident.can("connect"):
    st.info("Viewers can query data already decrypted in this session but cannot open connections.",
            icon=":material/visibility:")
    st.stop()

with st.expander("New connection", expanded=not sess.sources, icon=":material/add_link:"):
    kind = st.segmented_control("Source", ["Snowflake", "PostgreSQL"], default="Snowflake", key="conn_kind")
    d = cfg.snowflake
    if kind == "Snowflake":
        with st.form("sf"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Connection name", value=d.connection_name or "snowflake")
            account = c2.text_input("Account", value=d.account, placeholder="ORG-ACCOUNT")
            user = c1.text_input("User", value=d.user)
            secret = c2.text_input("PAT or password", type="password",
                                   placeholder="from secrets.toml" if d.pat else "",
                                   help="Leave empty to use the PAT from secrets.toml.")
            warehouse = c1.text_input("Warehouse", value=d.warehouse)
            role = c2.text_input("Role (optional)", value=d.role)
            database = c1.text_input("Database", value=d.database)
            schema = c2.text_input("Schema", value=d.schema)
            go = st.form_submit_button("Connect", icon=":material/link:", type="primary")
        if go:
            try:
                with st.spinner("Connecting…"):
                    src = connect_snowflake(name, account, user, secret or d.pat, database, schema, warehouse, role)
                sess.sources[name] = src
                state.audit("connect", f"{name} snowflake {account} as {user}")
                if cfg.settings["auto_decrypt_on_connect"]:
                    _load(src)
                st.rerun()
            except ConnectError as e:
                state.audit("connect_failed", str(e))
                st.error(str(e), icon=":material/error:")
    else:
        with st.form("pg"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Connection name", value="postgres")
            host = c2.text_input("Host", value="localhost")
            port = c1.number_input("Port", value=5432, step=1)
            database = c2.text_input("Database")
            user = c1.text_input("User")
            password = c2.text_input("Password", type="password")
            go = st.form_submit_button("Connect", icon=":material/link:", type="primary")
        if go:
            try:
                sess.sources[name] = connect_postgres(name, host, int(port), database, user, password)
                state.audit("connect", f"{name} postgres {host}")
                st.rerun()
            except ConnectError as e:
                st.error(str(e), icon=":material/error:")

# ---- encrypted tables --------------------------------------------------------
st.subheader("Encrypted tables", anchor=False)
with st.container(border=True):
    edited = st.data_editor(
        pd.DataFrame([{"table": t.table_name, "encrypted columns": ", ".join(t.encrypted_columns)}
                      for t in st.session_state.enc_tables] or [{"table": "", "encrypted columns": ""}]),
        num_rows="dynamic", hide_index=True, key="enc_editor",
        column_config={"table": st.column_config.TextColumn("Table (DB.SCHEMA.TABLE)", required=True),
                       "encrypted columns": st.column_config.TextColumn(help="Comma separated, or * to auto-detect")},
    )
    st.session_state.enc_tables = [
        EncryptedTable(r["table"].strip(), [c.strip() for c in str(r["encrypted columns"]).split(",") if c.strip()])
        for _, r in edited.iterrows() if str(r.get("table") or "").strip()
    ]
    st.caption("Changes apply to this session. Defaults come from `[[encrypted_tables]]` in secrets.toml.")
    if st.button("Decrypt and cache", icon=":material/lock_open:", type="primary", disabled=sess.primary is None):
        _load(sess.primary)

for r in st.session_state.get("load_reports", []):
    with st.container(border=True):
        if r.ok:
            cols = st.columns(4)
            cols[0].metric("Table", r.table.split(".")[-1], help=r.table)
            cols[1].metric("Rows", f"{r.rows:,}")
            cols[2].metric("Decrypted columns", len(r.decrypted_columns), help=", ".join(r.decrypted_columns))
            cols[3].metric("Failures", r.failures, help="Cells that did not decrypt with the configured key")
            if r.failures:
                st.warning(f"{r.failures} cells failed to decrypt – wrong key or corrupt data.", icon=":material/warning:")
            st.caption(f"Cached as `{r.entry.name}` in {r.seconds:.1f}s")
        else:
            st.error(f"{r.table}: {r.error}", icon=":material/error:")

if sess.cache.live_entries():
    st.page_link("app_pages/query.py", label="Query decrypted data", icon=":material/arrow_forward:")
