import streamlit as st


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif !important;
        }

        /* Hide Streamlit chrome */
        #MainMenu, footer, header { visibility: hidden; }

        /* Sidebar is no longer forcibly hidden; controls are moved into main pane */

        /* Rounded cover images */
        [data-testid="stImage"] img {
            border-radius: 6px;
        }

        /* Album card column hover */
        [data-testid="column"] > div:first-child {
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }
        [data-testid="column"] > div:first-child:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(232, 184, 75, 0.12);
        }

        /* Buttons */
        .stButton button {
            border-radius: 6px;
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            transition: background-color 0.15s, border-color 0.15s;
        }

        /* Tabs */
        [data-testid="stTabs"] [data-baseweb="tab"] {
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            padding: 8px 22px;
        }
        [data-testid="stTabs"] [aria-selected="true"] {
            border-bottom-color: #e8b84b !important;
            color: #e8b84b !important;
        }

        /* Metric cards */
        [data-testid="stMetric"] {
            background: #1c1c1c;
            border-radius: 8px;
            padding: 16px 20px;
            border: 1px solid #2a2a2a;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.8rem !important;
            font-weight: 600 !important;
            color: #e8b84b !important;
        }

        /* Expander */
        [data-testid="stExpander"] details {
            border: 1px solid #2a2a2a !important;
            border-radius: 8px;
            background: #161616;
        }

        /* AG Grid dark overrides */
        .ag-theme-alpine {
            --ag-background-color: #1a1a1a;
            --ag-header-background-color: #222222;
            --ag-odd-row-background-color: #1e1e1e;
            --ag-row-hover-color: #282828;
            --ag-selected-row-background-color: #2b2b18;
            --ag-border-color: #2a2a2a;
            --ag-header-foreground-color: #cccccc;
            --ag-foreground-color: #f0f0f0;
            --ag-secondary-foreground-color: #999999;
            --ag-font-family: 'Inter', sans-serif;
            --ag-font-size: 13px;
            --ag-cell-horizontal-border: none;
            --ag-range-selection-border-color: #e8b84b;
            --ag-checkbox-checked-color: #e8b84b;
            --ag-icon-color: #888888;
        }
        .ag-theme-alpine .ag-header-cell-label {
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .ag-theme-alpine .ag-row-group {
            font-weight: 600;
            color: #e8b84b;
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #1a1a1a; }
        ::-webkit-scrollbar-thumb { background: #3a3a3a; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #e8b84b; }

        /* Info tooltip */
        .info-tooltip {
            position: relative;
            display: inline-block;
            cursor: help;
            font-size: 1rem;
            line-height: 1;
        }
        .info-tooltip .tooltip-text {
            visibility: hidden;
            opacity: 0;
            background-color: #2a2a2a;
            color: #f0f0f0;
            text-align: left;
            border-radius: 6px;
            padding: 10px 14px;
            position: absolute;
            z-index: 9999;
            bottom: 140%;
            left: 50%;
            transform: translateX(-50%);
            min-width: 330px;
            border: 1px solid #444;
            font-size: 0.82rem;
            line-height: 1.65;
            box-shadow: 0 4px 16px rgba(0,0,0,0.5);
            transition: opacity 0.15s ease;
            pointer-events: none;
        }
        .info-tooltip:hover .tooltip-text {
            visibility: visible;
            opacity: 1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
