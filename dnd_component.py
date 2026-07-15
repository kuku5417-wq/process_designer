"""dnd_component.py — SortableJS 기반 컬럼 드릴다운 보드 (Streamlit 정적 커스텀 컴포넌트).

npm 빌드 없이 동작한다: declare_component(path=frontend) 로 폴더를 그대로 서빙하고,
index.html 이 Streamlit 의 postMessage 프로토콜을 직접 구현한다. SortableJS 는 로컬에
번들되어 있어 런타임 CDN/인터넷 의존이 없다 (사내망·사외망 모두 동작).

주의 — 컴포넌트가 파이썬으로 되돌려주는 값은 **전체 트리가 아니라 이벤트 1건**이다.
전체 상태를 보내면 보이는 4컬럼만 담긴 부분 상태가 전체 트리를 덮어써 데이터가 날아간다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_FRONTEND = Path(__file__).resolve().parent / "frontend"
_REQUIRED = ("index.html", "sortable.min.js")

_component = None


@lru_cache(maxsize=1)
def is_available() -> bool:
    """DnD 컴포넌트를 쓸 수 있는지 선검사 (프론트 자산 + pyarrow).

    Streamlit 의 components.v1.custom_component.create_instance 가 pyarrow 를 import 하므로,
    pyarrow 가 없으면 컴포넌트 호출이 StreamlitAPIException 으로 죽는다. parquet 을 쓰지
    않더라도 의존성이 필요한 이유다.
    """
    if not all((_FRONTEND / f).exists() for f in _REQUIRED):
        return False
    try:
        import pyarrow  # noqa: F401
    except Exception:
        return False
    return True


def missing_assets() -> list[str]:
    return [f for f in _REQUIRED if not (_FRONTEND / f).exists()]


def _get():
    global _component
    if _component is None:
        _component = components.declare_component("process_board", path=str(_FRONTEND))
    return _component


def render_board(columns: list[dict], height: int = 620, key: str = "pd_board") -> dict | None:
    """보드를 그리고 마지막 사용자 이벤트를 반환.

    key 는 반드시 고정값이어야 한다 — Streamlit 은 커스텀 컴포넌트 위젯 ID 를 name/url 로만
    잡으므로(key_as_main_identity), key 가 있으면 args 가 바뀌어도 값이 default 로 리셋되지 않는다.
    """
    return _get()(columns=columns, height=height, key=key, default=None)


def take_event(raw: dict | None, state_key: str = "pd_last_evt") -> dict | None:
    """같은 이벤트의 중복 처리 차단.

    리런되면 컴포넌트는 직전 값을 그대로 다시 돌려주므로, evt_id 로 한 번만 처리한다.
    단조 증가 seq 를 쓰면 안 된다 — iframe 이 새로고침되면 카운터가 1로 리셋되는데 서버의
    last_seq 는 그대로라 이후 모든 이벤트가 영영 무시된다. UUID 는 리로드에 면역이다.
    """
    if not isinstance(raw, dict):
        return None
    eid = raw.get("evt_id")
    if not eid:
        return None
    if eid == st.session_state.get(state_key):
        return None
    st.session_state[state_key] = eid
    return raw
