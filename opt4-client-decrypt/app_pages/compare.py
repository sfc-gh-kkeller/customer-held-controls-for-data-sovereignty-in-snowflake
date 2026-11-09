import time

import pandas as pd
import plotly.express as px
import streamlit as st

from secure_analytics import smart_query
from secure_analytics.sql_guard import UnsafeSQL, check_read_only
from ui import state

cfg = state.config()
sess = state.session(cfg)
state.require("compare")

st.header("Compare", anchor=False)
st.caption("Run queries side by side across the local cache and connected sources.")

# Targets: the local cache plus every live connection.
LOCAL = "Local cache (decrypted)"
targets = [LOCAL] + list(sess.sources)
cap = int(cfg.settings["max_live_rows"])


def run_on(target: str, sql: str) -> pd.DataFrame:
    if target == LOCAL:
        return smart_query.run(sql, "cache", sess.cache, None, cap).df
    return sess.sources[target].query(check_read_only(sql).sql, cap)


if len(targets) == 1 and not sess.cache.live_entries():
    state.empty_state("Connect a source or decrypt a table to compare data.", ":material/compare_arrows:",
                      "app_pages/connect.py", "Go to connect")
    st.stop()

first = next(iter(sess.cache.live_entries().values()), None)
default_q = (f"SELECT DEPARTMENT, ROUND(AVG(SALARY), 0) AS value FROM {first.source} GROUP BY 1 ORDER BY 1"
             if first and "DEPARTMENT" in first.columns and "SALARY" in first.columns else "SELECT 1 AS k, 1 AS value")

view = st.segmented_control("Tool", ["Side by side", "Consolidate", "Performance"], default="Side by side",
                            key="compare_view", required=True)

if view == "Side by side":
    a, b = st.columns(2)
    with a.container(border=True):
        ta = st.selectbox("Target A", targets, key="cmp_a")
        qa = st.text_area("Query A", default_q, height=120, key="cmp_qa")
    with b.container(border=True):
        tb = st.selectbox("Target B", targets, index=min(1, len(targets) - 1), key="cmp_b")
        qb = st.text_area("Query B", default_q, height=120, key="cmp_qb")
    st.caption("Tip: compare the decrypted cache with the live source to show the source only holds ciphertext.")
    if st.button("Compare", icon=":material/compare_arrows:", type="primary"):
        try:
            ra, rb = run_on(ta, qa), run_on(tb, qb)
            state.audit("compare", f"{ta} vs {tb}")
            r1, r2 = st.columns(2)
            r1.dataframe(ra, hide_index=True, height=300, alt="Result A")
            r2.dataframe(rb, hide_index=True, height=300, alt="Result B")
            if len(ra.columns) >= 2 and len(rb.columns) >= 2:
                long = pd.concat([
                    pd.DataFrame({"category": ra.iloc[:, 0].astype(str), "value": pd.to_numeric(ra.iloc[:, 1], errors="coerce"), "source": f"A · {ta}"}),
                    pd.DataFrame({"category": rb.iloc[:, 0].astype(str), "value": pd.to_numeric(rb.iloc[:, 1], errors="coerce"), "source": f"B · {tb}"}),
                ])
                g1, g2 = st.columns([3, 1])
                g1.plotly_chart(px.bar(long, x="category", y="value", color="source", barmode="group"),
                                alt="Comparison chart")
                sa, sb = long[long.source.str.startswith("A")].value.sum(), long[long.source.str.startswith("B")].value.sum()
                g2.metric("Total A", f"{sa:,.2f}", border=True)
                g2.metric("Total B", f"{sb:,.2f}", border=True)
                g2.metric("Difference", f"{sb - sa:,.2f}", f"{((sb - sa) / sa * 100) if sa else 0:.1f}%", border=True)
        except (UnsafeSQL, Exception) as e:
            st.error(str(e).splitlines()[0][:300], icon=":material/error:")

elif view == "Consolidate":
    st.session_state.setdefault("consolidation", [])
    with st.form("add_cons", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 4, 1], vertical_alignment="bottom")
        t = c1.selectbox("Target", targets)
        q = c2.text_input("Query", placeholder="SELECT * FROM … LIMIT 100")
        if c3.form_submit_button("Add", icon=":material/add:") and q:
            st.session_state.consolidation.append({"target": t, "query": q})
    for i, item in enumerate(st.session_state.consolidation):
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.badge(item["target"], color="blue")
            st.code(item["query"], language="sql", width="stretch")
            if st.button("Remove", icon=":material/delete:", key=f"rm_{i}"):
                st.session_state.consolidation.pop(i)
                st.rerun()
    if st.session_state.consolidation and st.button("Consolidate", icon=":material/merge:", type="primary"):
        try:
            parts = []
            for item in st.session_state.consolidation:
                d = run_on(item["target"], item["query"])
                d["_source"] = item["target"]
                parts.append(d)
            combined = pd.concat(parts, ignore_index=True, sort=False)
            state.set_result(combined, "consolidation")
            state.audit("consolidate", f"{len(parts)} queries, {len(combined)} rows")
            st.success(f"Combined {len(parts)} results into {len(combined):,} rows.", icon=":material/check_circle:")
            c1, c2 = st.columns([3, 1])
            c1.dataframe(combined, hide_index=True, height=360, alt="Consolidated rows")
            summary = combined.groupby("_source").size().reset_index(name="rows")
            c2.plotly_chart(px.pie(summary, values="rows", names="_source", hole=0.5), alt="Rows by source")
            st.page_link("app_pages/explore.py", label="Explore the combined result", icon=":material/insights:")
        except Exception as e:
            st.error(str(e).splitlines()[0][:300], icon=":material/error:")

else:
    q = st.text_area("Query run on every target", "SELECT COUNT(*) AS n FROM " + (first.source if first else "t"),
                     height=90)
    if st.button("Run benchmark", icon=":material/speed:", type="primary"):
        rows = []
        for t in targets:
            t0 = time.time()
            try:
                n = len(run_on(t, q))
                rows.append({"target": t, "ms": round((time.time() - t0) * 1000, 1), "rows": n, "status": "ok"})
            except Exception as e:
                rows.append({"target": t, "ms": None, "rows": 0, "status": str(e).splitlines()[0][:80]})
        perf = pd.DataFrame(rows)
        st.dataframe(perf, hide_index=True, alt="Benchmark results")
        ok = perf[perf.status == "ok"]
        if len(ok) > 1:
            st.bar_chart(ok.set_index("target")["ms"], alt="Execution time per target", y_label="ms")
