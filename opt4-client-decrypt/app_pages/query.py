import time

import streamlit as st

from secure_analytics import smart_query
from secure_analytics.sql_guard import UnsafeSQL
from ui import state

cfg = state.config()
sess = state.session(cfg)
ident = state.identity()

st.header("Query", anchor=False)

entries = sess.cache.live_entries()
modes = {"cache": "Local cache", "smart": "Smart (cache + live)", "live": "Live source"}
allowed = [m for m in smart_query.MODES if m == "cache" or ident.can("query_live")]

left, right = st.columns([3, 1], gap="medium")

with right:
    st.markdown("**Cached tables**")
    if not entries:
        st.caption("Nothing decrypted yet.")
        st.page_link("app_pages/connect.py", label="Connect and decrypt", icon=":material/arrow_forward:")
    for e in entries.values():
        with st.container(border=True):
            st.markdown(f"`{e.name}`")
            st.caption(f"{e.rows:,} rows · {len(e.columns)} cols · expires {time.strftime('%H:%M', time.localtime(e.expires_at))}")
            if e.decrypted_columns:
                st.caption(":material/lock_open: " + ", ".join(e.decrypted_columns))
            if st.button("Preview", key=f"pv_{e.name}", icon=":material/visibility:", width="stretch"):
                st.session_state.sql = f"SELECT * FROM {e.source} LIMIT 100"

with left:
    first = next(iter(entries.values()), None)
    examples = {}
    if first:
        dec = [c for c in first.decrypted_columns if c in first.columns]
        num = [c for c in dec if "SALARY" in c.upper()] or dec[-1:] or first.columns[-1:]
        cat = [c for c in first.columns if c not in first.decrypted_columns and "DEP" in c.upper()] or first.columns[:1]
        examples = {
            "Preview": f"SELECT * FROM {first.source} LIMIT 100",
            "Aggregate decrypted values": f"SELECT {cat[0]}, COUNT(*) AS n, ROUND(AVG({num[0]}), 2) AS avg_{num[0].lower()}\n"
                                          f"FROM {first.source}\nGROUP BY 1\nORDER BY 3 DESC",
            "Ciphertext at the source (live)": f"SELECT * FROM {first.source} LIMIT 5",
        }
    st.session_state.setdefault("sql", next(iter(examples.values()), "SELECT 1"))

    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    st.session_state.setdefault("query_mode", "cache")
    if st.session_state.query_mode not in allowed:
        st.session_state.query_mode = "cache"
    mode = c1.segmented_control("Mode", allowed, format_func=modes.get, key="query_mode", required=True)
    if examples:
        def _pick(q: str, live: bool) -> None:
            st.session_state.sql = q
            st.session_state.query_mode = "live" if live and "live" in allowed else "cache"

        with c2.popover("Examples", icon=":material/lightbulb:", width="stretch"):
            for label, q in examples.items():
                st.button(label, key=f"ex_{label}", width="stretch", on_click=_pick, args=(q, "live" in label))

    help_text = {
        "cache": "Runs locally in DuckDB on decrypted data. Write source names (DB.SCHEMA.TABLE) or cache names.",
        "smart": "Cached tables are used locally; other tables are fetched live (row-capped) just for this query.",
        "live": "Runs on the source. Encrypted columns come back as ciphertext – nothing is decrypted.",
    }
    st.caption(help_text[mode])
    sql = st.text_area("SQL", key="sql", height=180, label_visibility="collapsed")
    run = st.button("Run", icon=":material/play_arrow:", type="primary", shortcut="ctrl+enter")

    if run:
        try:
            t0 = time.time()
            with st.spinner("Running…"):
                res = smart_query.run(sql, mode, sess.cache, sess.primary, int(cfg.settings["max_live_rows"]))
            st.session_state.query_result = (res, time.time() - t0)
            state.set_result(res.df, f"{mode} query")
            state.audit(f"query_{mode}", sql.replace("\n", " "))
        except UnsafeSQL as e:
            st.session_state.pop("query_result", None)
            st.error(str(e), icon=":material/block:")
            state.audit("query_rejected", f"{e}: {sql[:120]}")
        except Exception as e:
            st.session_state.pop("query_result", None)
            st.error(f"Query failed: {str(e).splitlines()[0][:300]}", icon=":material/error:")

    if "query_result" in st.session_state:
        res, secs = st.session_state.query_result
        with st.container(border=True):
            row = st.container(horizontal=True, vertical_alignment="center")
            row.badge(modes[res.mode], icon=":material/route:", color="blue")
            row.badge(f"{len(res.df):,} rows · {secs * 1000:.0f} ms", icon=":material/timer:", color="gray")
            for t in res.live_tables:
                row.badge(f"live: {t}", icon=":material/cloud:", color="orange")
            st.dataframe(res.df, hide_index=True, height=min(420, 38 + 35 * max(1, len(res.df))),
                         alt="Query result")
            r2 = st.container(horizontal=True)
            r2.page_link("app_pages/explore.py", label="Explore this result", icon=":material/insights:")
            if cfg.settings["csv_export_enabled"]:
                if r2.download_button("Download CSV", res.df.to_csv(index=False), "result.csv", "text/csv",
                                      icon=":material/download:", on_click="ignore"):
                    state.audit("export_csv", f"{len(res.df)} rows")
            with st.expander("Executed SQL", icon=":material/code:"):
                st.code(res.executed_sql, language="sql")
