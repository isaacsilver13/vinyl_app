from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

_LINK_RENDERER = JsCode("""
class LinkRenderer {
    init(params) {
        this.eGui = document.createElement('a');
        this.eGui.href = params.value || '#';
        this.eGui.target = '_blank';
        this.eGui.rel = 'noopener noreferrer';
        this.eGui.innerText = 'View';
        this.eGui.style.color = '#e8b84b';
        this.eGui.style.textDecoration = 'none';
        this.eGui.onmouseover = () => { this.eGui.style.textDecoration = 'underline'; };
        this.eGui.onmouseout  = () => { this.eGui.style.textDecoration = 'none'; };
    }
    getGui() { return this.eGui; }
    refresh() { return false; }
}
""")

_GRID_CUSTOM_CSS = {
    ".ag-root-wrapper": {
        "background-color": "#1a1a1a !important",
        "border": "1px solid #2a2a2a !important",
        "color": "#f0f0f0",
        "font-family": "'Inter', sans-serif",
        "font-size": "13px",
    },
    ".ag-header": {
        "background-color": "#222222 !important",
        "border-bottom": "1px solid #2a2a2a !important",
    },
    ".ag-header-cell": {
        "background-color": "#222222 !important",
        "border-right": "none !important",
    },
    ".ag-header-cell-text": {
        "color": "#cccccc !important",
        "font-weight": "600",
        "font-size": "12px",
        "text-transform": "uppercase",
        "letter-spacing": "0.04em",
    },
    ".ag-row": {
        "background-color": "#1a1a1a !important",
        "border-bottom": "1px solid #2a2a2a !important",
        "color": "#f0f0f0 !important",
    },
    ".ag-row-odd": {
        "background-color": "#1e1e1e !important",
    },
    ".ag-row-selected": {
        "background-color": "#2b2b18 !important",
    },
    ".ag-cell": {
        "border-right": "none !important",
        "color": "#f0f0f0 !important",
    },
    ".ag-row-group-leaf-indent .ag-row-group-indent": {
        "background-color": "transparent",
    },
    ".ag-group-value": {
        "color": "#e8b84b !important",
        "font-weight": "600",
    },
    ".ag-icon": {
        "color": "#888888 !important",
    },
    ".ag-paging-panel": {
        "background-color": "#1a1a1a !important",
        "color": "#aaaaaa",
        "border-top": "1px solid #2a2a2a !important",
    },
    ".ag-input-field-input": {
        "background-color": "#222 !important",
        "color": "#f0f0f0 !important",
        "border": "1px solid #444 !important",
    },
    ".ag-column-drop": {
        "background-color": "#1a1a1a !important",
        "border-bottom": "1px solid #2a2a2a !important",
        "color": "#f0f0f0 !important",
    },
    ".ag-column-drop-title-bar": {
        "color": "#aaaaaa !important",
        "font-size": "11px !important",
        "text-transform": "uppercase !important",
        "letter-spacing": "0.04em !important",
    },
    ".ag-column-drop-cell": {
        "background-color": "#2a2a2a !important",
        "border": "1px solid #3a3a3a !important",
        "color": "#e8b84b !important",
        "border-radius": "4px !important",
    },
    ".ag-column-drop-cell-button": {
        "color": "#888888 !important",
    },
    ".ag-column-drop-empty-message": {
        "color": "#555555 !important",
    },
}


def _make_grid(
    df: pd.DataFrame,
    *,
    height: int = 400,
    row_group_cols: list[str] | None = None,
    link_cols: list[str] | None = None,
    hidden_cols: list[str] | None = None,
    selection_mode: str | None = None,
    default_sort_col: str | None = None,
    default_sort_asc: bool = True,
    col_widths: dict[str, int] | None = None,
    col_formatters: dict[str, "JsCode"] | None = None,
    key: str | None = None,
) -> dict:
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        filter=True,
        sortable=True,
        resizable=True,
        minWidth=80,
        wrapText=False,
        autoHeight=False,
        enableRowGroup=True,
    )
    gb.configure_grid_options(
        rowHeight=36,
        headerHeight=40,
        suppressMovableColumns=False,
        enableCellTextSelection=True,
        rowGroupPanelShow="always" if row_group_cols else "never",
        onFirstDataRendered=JsCode("function(params) { params.api.autoSizeAllColumns(); }") if not col_widths else None,
    )
    if row_group_cols:
        gb.configure_grid_options(
            groupDisplayType="singleColumn",
            groupDefaultExpanded=0,
            autoGroupColumnDef={"minWidth": 200, "cellRendererParams": {"suppressCount": False}},
        )
        for i, col in enumerate(row_group_cols):
            gb.configure_column(col, rowGroup=True, rowGroupIndex=i, hide=True)
    if link_cols:
        for col in link_cols:
            gb.configure_column(col, cellRenderer=_LINK_RENDERER, filter=False, sortable=False)
    if hidden_cols:
        for col in hidden_cols:
            gb.configure_column(col, hide=True)
    if default_sort_col and default_sort_col in df.columns:
        gb.configure_column(
            default_sort_col,
            sort="asc" if default_sort_asc else "desc",
        )
    if col_widths:
        for col, w in col_widths.items():
            gb.configure_column(col, width=w, suppressSizeToFit=True)
    if col_formatters:
        for col, fmt in col_formatters.items():
            gb.configure_column(col, valueFormatter=fmt)
    if selection_mode:
        gb.configure_selection(selection_mode, use_checkbox=True)
    go_opts = gb.build()
    return AgGrid(
        df,
        gridOptions=go_opts,
        height=height,
        update_mode=GridUpdateMode.SELECTION_CHANGED if selection_mode else GridUpdateMode.NO_UPDATE,
        allow_unsafe_jscode=True,
        enable_enterprise_modules=True,
        theme="alpine",
        custom_css=_GRID_CUSTOM_CSS,
        key=key,
    )


def _plotly_bar(
    labels: list,
    values: list,
    key: str,
    height: int = 280,
) -> str | None:
    """Render a dark-themed interactive bar chart. Returns the clicked bar label or None."""
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color="#e8b84b",
            marker_line_width=0,
            hovertemplate="%{x}<br>%{y} records<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=50),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#aaaaaa", family="Inter", size=12),
        xaxis=dict(
            gridcolor="#252525",
            tickfont=dict(color="#888"),
            showline=False,
            tickangle=-30,
        ),
        yaxis=dict(
            gridcolor="#252525",
            tickfont=dict(color="#888"),
            showline=False,
        ),
        height=height,
        bargap=0.3,
        hoverlabel=dict(bgcolor="#222", font_color="#f0f0f0", bordercolor="#444"),
        clickmode="event+select",
        dragmode="zoom",
        modebar_remove=["select2d", "lasso2d"],
    )
    event = st.plotly_chart(
        fig,
        on_select="rerun",
        use_container_width=True,
        key=key,
    )
    if event and event.selection and event.selection.points:
        return str(event.selection.points[0]["x"])
    return None


def _calendar_heatmap(play_counts: dict) -> go.Figure:
    """GitHub-style calendar heatmap of listening activity for the past year."""
    today = date.today()
    start = today - timedelta(days=364)
    start = start - timedelta(days=start.weekday())   # align to Monday
    n_weeks = (today - start).days // 7 + 2

    z: list[list] = [[0] * n_weeks for _ in range(7)]
    text: list[list] = [[""] * n_weeks for _ in range(7)]

    d = start
    for week in range(n_weeks):
        for dow in range(7):
            ds = d.isoformat()
            if d <= today:
                count = play_counts.get(ds, 0)
                z[dow][week] = count
                text[dow][week] = f"{ds}: {count} play{'s' if count != 1 else ''}"
            else:
                z[dow][week] = None
            d += timedelta(days=1)

    max_count = max(play_counts.values()) if play_counts else 1

    # Month label ticks
    tick_vals: list[int] = []
    tick_text: list[str] = []
    d2 = start
    for week in range(n_weeks):
        if d2 <= today and d2.day <= 7:
            tick_vals.append(week)
            tick_text.append(d2.strftime("%b"))
        d2 += timedelta(weeks=1)

    fig = go.Figure(go.Heatmap(
        z=z,
        text=text,
        hovertemplate="%{text}<extra></extra>",
        colorscale=[
            [0.0,   "#222222"],
            [0.001, "#2d2400"],
            [0.3,   "#7a5500"],
            [0.7,   "#c08800"],
            [1.0,   "#e8b84b"],
        ],
        showscale=False,
        xgap=3,
        ygap=3,
        zmin=0,
        zmax=max(max_count, 1),
    ))
    fig.update_layout(
        margin=dict(l=40, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#aaaaaa", family="Inter", size=11),
        xaxis=dict(
            showgrid=False, zeroline=False, showline=False,
            tickvals=tick_vals, ticktext=tick_text,
            tickfont=dict(color="#888", size=11),
        ),
        yaxis=dict(
            showgrid=False, zeroline=False, showline=False,
            tickvals=list(range(7)),
            ticktext=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            tickfont=dict(color="#888", size=11),
            autorange="reversed",
        ),
        height=160,
    )
    return fig


def _plotly_hbar(labels: list, values: list, height: int = 280) -> None:
    """Dark-themed horizontal bar chart (no click interaction)."""
    fig = go.Figure(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color="#e8b84b",
        marker_line_width=0,
        hovertemplate="%{y}<br>%{x} plays<extra></extra>",
    ))
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#aaaaaa", family="Inter", size=12),
        xaxis=dict(gridcolor="#252525", tickfont=dict(color="#888"), showline=False),
        yaxis=dict(
            gridcolor="#252525",
            tickfont=dict(color="#f0f0f0", size=11),
            showline=False,
            autorange="reversed",
        ),
        height=height,
        bargap=0.35,
        hoverlabel=dict(bgcolor="#222", font_color="#f0f0f0", bordercolor="#444"),
    )
    st.plotly_chart(fig, use_container_width=True)
