"""ui_styles.py — 화면 CSS (산업형 톤, 형제 앱과 동일 계열)."""
from __future__ import annotations

import streamlit as st

_CSS = """
<style>
  .block-container { padding-top: 2.2rem; max-width: 1600px; }

  /* 사이드바 브랜드 */
  .pd-brand { font-size: 1.05rem; font-weight: 700; color: #0f172a; line-height: 1.3; }
  .pd-brand span { display:block; font-size: .75rem; font-weight: 400; color: #64748b; margin-top: .15rem; }
  .pd-env { font-size: .75rem; color: #475569; background: #f1f5f9; border-radius: 6px;
            padding: .3rem .5rem; margin: .5rem 0 .8rem; word-break: break-all; }

  /* 고정 계층 브레드크럼 */
  .pd-fixed { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; margin-bottom:.6rem; }
  .pd-fixed .chip { background:#e2e8f0; color:#334155; border-radius:999px;
                    padding:.15rem .6rem; font-size:.78rem; font-weight:600; }
  .pd-fixed .chip.lock::before { content:"🔒 "; }
  .pd-fixed .sep { color:#94a3b8; font-size:.8rem; }
  .pd-fixed .chip.cur { background:#1f6feb; color:#fff; }

  /* 폴백(간단 모드) 카드 */
  .pd-card { border:1px solid #e2e8f0; border-left:3px solid #cbd5e1; border-radius:6px;
             padding:.4rem .55rem; margin-bottom:.3rem; background:#fff; }
  .pd-card.sel { border-left-color:#1f6feb; background:#eff6ff; }
  .pd-card .nm { font-size:.85rem; font-weight:600; color:#0f172a; }
  .pd-card .meta { font-size:.7rem; color:#64748b; margin-top:.15rem; }
  .pd-badge { display:inline-block; background:#dcfce7; color:#166534; border-radius:4px;
              padding:0 .3rem; font-size:.65rem; font-weight:700; margin-left:.25rem; }
  .pd-colhead { font-size:.78rem; font-weight:700; color:#475569; border-bottom:2px solid #e2e8f0;
                padding-bottom:.25rem; margin-bottom:.45rem; }
  .pd-empty { color:#94a3b8; font-size:.75rem; padding:.5rem 0; }

  /* 저장 필요 배지 */
  .pd-dirty { background:#fef3c7; color:#92400e; border-radius:6px; padding:.35rem .55rem;
              font-size:.78rem; font-weight:600; text-align:center; margin-bottom:.4rem; }
  .pd-clean { background:#f1f5f9; color:#64748b; border-radius:6px; padding:.35rem .55rem;
              font-size:.75rem; text-align:center; margin-bottom:.4rem; }

  section[data-testid="stSidebar"] .stButton button { width: 100%; }
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
