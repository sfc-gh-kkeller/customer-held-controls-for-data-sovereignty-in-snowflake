import pandas as pd
import plotly.express as px
import streamlit as st

from ui import charts, state

cfg = state.config()
sess = state.session(cfg)

st.header("Explore", anchor=False)

df = sess.last_result
if df is None or df.empty:
    state.empty_state("Run a query first – its result appears here for charts, filters and statistics.",
                      ":material/insights:", "app_pages/query.py", "Go to query")
    st.stop()

df = charts.coerce_dates(df)
numeric, categorical, dates = charts.split_columns(df)
st.caption(f"Source: {sess.last_result_label}")

m = st.columns(4)
m[0].metric("Rows", f"{len(df):,}", border=True)
m[1].metric("Numeric columns", len(numeric), border=True)
m[2].metric("Categorical columns", len(categorical), border=True)
m[3].metric("Null cells", f"{df.isna().mean().mean() * 100:.1f}%", border=True)

# ---- filters ---------------------------------------------------------------
fdf = df
with st.expander("Filters", icon=":material/filter_alt:"):
    fc = st.columns(4)
    for i, col in enumerate(categorical[:4]):
        vals = df[col].dropna().unique()
        with fc[i]:
            if len(vals) <= 50:
                sel = st.multiselect(col, sorted(map(str, vals)), key=f"f_{col}", placeholder="All")
                if sel:
                    fdf = fdf[fdf[col].astype(str).isin(sel)]
            else:
                st.caption(f"{col}: {len(vals)} distinct values – too many to filter")
    if dates:
        dc1, dc2 = st.columns(2)
        dcol = dc1.selectbox("Date column", dates)
        lo, hi = df[dcol].min(), df[dcol].max()
        rng = dc2.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)
        if isinstance(rng, tuple) and len(rng) == 2:
            fdf = fdf[(fdf[dcol] >= pd.to_datetime(rng[0])) & (fdf[dcol] <= pd.to_datetime(rng[1]))]
if len(fdf) != len(df):
    st.caption(f":material/filter_alt: Showing {len(fdf):,} of {len(df):,} rows")

# ---- chart -----------------------------------------------------------------
with st.container(border=True):
    c = st.columns(5, vertical_alignment="bottom")
    kind = c[0].selectbox("Chart", charts.CHART_TYPES)
    x_opts = categorical + dates + numeric
    x = c[1].selectbox("X", x_opts)
    y = c[2].selectbox("Y" if kind != "Pie" else "Value", numeric) if kind in charts.NEEDS_Y and numeric else None
    color = c[3].selectbox("Color by", ["—"] + categorical)
    color = None if color == "—" else color
    size = c[4].selectbox("Size by", ["—"] + numeric) if kind == "Scatter" else "—"
    size = None if size == "—" else size
    if fdf.empty or not x:
        st.info("No data for the current filters.", icon=":material/filter_alt_off:")
    elif kind in charts.NEEDS_Y and not y:
        st.info("This chart needs a numeric column.", icon=":material/info:")
    else:
        try:
            fig = charts.build(kind, fdf, x, y, color, size)
            fig.update_layout(height=480, margin=dict(t=48, l=8, r=8, b=8))
            st.plotly_chart(fig, alt=f"{kind} chart")
        except Exception as e:
            st.warning(f"Cannot draw this chart: {e}", icon=":material/warning:")

# ---- statistics ------------------------------------------------------------
if numeric:
    view = st.segmented_control("Details", ["Statistics", "Correlation", "Data quality", "Rows"],
                                default="Statistics", key="explore_view")
    if view == "Statistics":
        st.dataframe(fdf[numeric].describe().T, alt="Descriptive statistics")
    elif view == "Correlation":
        if len(numeric) > 1:
            fig = px.imshow(fdf[numeric].corr(), text_auto=".2f", color_continuous_scale="RdBu", zmin=-1, zmax=1)
            fig.update_layout(height=420, margin=dict(t=16, l=8, r=8, b=8))
            st.plotly_chart(fig, alt="Correlation heatmap")
        else:
            st.caption("Needs at least two numeric columns.")
    elif view == "Data quality":
        missing = fdf.isna().sum()
        missing = missing[missing > 0].sort_values(ascending=False)
        if missing.empty:
            st.success("No missing values.", icon=":material/check_circle:")
        else:
            st.bar_chart(missing, alt="Missing values per column")
    else:
        st.dataframe(fdf, hide_index=True, alt="Filtered rows")
