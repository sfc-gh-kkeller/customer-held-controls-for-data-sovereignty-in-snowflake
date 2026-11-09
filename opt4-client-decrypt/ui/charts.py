"""Chart builders for the Explore page (Plotly – kept for violin/box/heatmap coverage)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px

CHART_TYPES = ["Bar", "Line", "Scatter", "Histogram", "Box", "Pie", "Heatmap", "Area", "Violin"]
NEEDS_Y = {"Bar", "Line", "Scatter", "Box", "Pie", "Heatmap", "Area", "Violin"}


def split_columns(df: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    numeric = df.select_dtypes("number").columns.tolist()
    dates = df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()
    categorical = [c for c in df.columns if c not in numeric and c not in dates]
    return numeric, categorical, dates


def coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object and any(k in c.lower() for k in ("date", "time", "_at")):
            parsed = pd.to_datetime(out[c], errors="coerce")
            if parsed.notna().mean() > 0.9:
                out[c] = parsed
    return out


def build(kind: str, df: pd.DataFrame, x: str, y: str | None, color: str | None, size: str | None):
    if kind == "Bar":
        if y:
            agg = df.groupby([x] + ([color] if color else []), dropna=False)[y].sum().reset_index()
            return px.bar(agg, x=x, y=y, color=color, title=f"{y} by {x}")
        return px.bar(df[x].value_counts().reset_index(), x=x, y="count", title=f"Count by {x}")
    if kind == "Line":
        return px.line(df.sort_values(x), x=x, y=y, color=color, title=f"{y} over {x}")
    if kind == "Scatter":
        return px.scatter(df, x=x, y=y, color=color, size=size, title=f"{y} vs {x}")
    if kind == "Histogram":
        return px.histogram(df, x=x, color=color, title=f"Distribution of {x}")
    if kind == "Box":
        return px.box(df, x=x, y=y, color=color, title=f"{y} by {x}")
    if kind == "Pie":
        return px.pie(df.groupby(x)[y].sum().reset_index(), values=y, names=x, title=f"{y} share by {x}")
    if kind == "Heatmap":
        if not color:
            raise ValueError("Heatmap needs a 'color by' column for the second axis.")
        return px.imshow(df.pivot_table(values=y, index=x, columns=color, aggfunc="mean"),
                         aspect="auto", title=f"Mean {y}")
    if kind == "Area":
        return px.area(df.sort_values(x), x=x, y=y, color=color, title=f"{y} over {x}")
    if kind == "Violin":
        return px.violin(df, x=x, y=y, color=color, box=True, title=f"{y} by {x}")
    raise ValueError(kind)
