from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

_NOTABLE_DESCS = {
    "180g", "180 Gram", "Gatefold", "Limited Edition", "Reissue",
    "Remastered", "Picture Disc", "Numbered", "Promo", "Test Pressing",
    "White Label", "Etched", "Colored",
}


def _decade(year, override=None) -> str | None:
    y = override or year
    if not y:
        return None
    return f"{(int(y) // 10) * 10}s"


def _format_last_sync(ts: str | None) -> str:
    if not ts:
        return "Never"
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "Less than 1 hour ago"
    if hours == 1:
        return "1 hour ago"
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''} ago"


def _cover(url: str | None, width: int = 120) -> None:
    if url:
        st.image(url, width=width)
    else:
        st.markdown(
            f"<div style='width:{width}px;height:{width}px;background:#222;"
            f"display:flex;align-items:center;justify-content:center;"
            f"border-radius:6px;color:#555;font-size:13px;letter-spacing:0.02em'>no cover</div>",
            unsafe_allow_html=True,
        )


def _color_dot(_text: str) -> str:
    return ""


def _pressing_badge(r: dict) -> str:
    color_text = r.get("format_text") or ""
    descs      = r.get("format_descriptions") or []
    notable    = [d for d in descs if d in _NOTABLE_DESCS]
    parts: list[str] = []
    if color_text:
        dot = _color_dot(color_text)
        parts.append(f"{dot} {color_text}".strip())
    parts.extend(notable)
    return "  ·  ".join(parts)
