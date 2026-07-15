"""state.py — session_state 헬퍼 (트리 편집 상태 · 선택 · 변경 카운터).

편집은 세션 메모리에만 반영하고, 디스크 저장은 사이드바 [저장] 버튼에서만 일어난다.
드래그마다 저장하면 스냅샷 폴더가 폭발하고 작성자 기록이 무의미해진다.
"""
from __future__ import annotations

import os
from typing import Any

import streamlit as st

import schema
import store

SEL_KEY = "sel"


def init() -> None:
    if "data" not in st.session_state:
        data, warns = store.load_tree()
        st.session_state["data"] = data
        st.session_state["load_warns"] = warns
        st.session_state["dirty"] = 0
    if SEL_KEY not in st.session_state:
        st.session_state[SEL_KEY] = {lv: None for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1)}
    if "author" not in st.session_state:
        # 공용PC라 USERNAME 은 부정확하다 — 수정 가능한 기본값으로만 쓴다
        st.session_state["author"] = os.environ.get("USERNAME", "") or ""
    st.session_state.setdefault("dnd_enabled", True)
    st.session_state.setdefault("dirty", 0)
    st.session_state.setdefault("disk_seen_mtime", 0.0)


def data() -> dict:
    return st.session_state["data"]


def author() -> str:
    return (st.session_state.get("author") or "").strip()


def touch() -> None:
    """변경 1건 기록 (저장 필요 표시)."""
    st.session_state["dirty"] = st.session_state.get("dirty", 0) + 1


def dirty() -> int:
    return st.session_state.get("dirty", 0)


def mark_saved() -> None:
    st.session_state["dirty"] = 0


def reload_from_disk() -> list[str]:
    d, warns = store.load_tree()
    st.session_state["data"] = d
    st.session_state["dirty"] = 0
    prune_selection()
    return warns


# ── 선택 ────────────────────────────────────────────────

def sel() -> dict[int, str | None]:
    return st.session_state[SEL_KEY]


def select(level: int, node_id: str | None) -> None:
    """레벨 선택. 더 깊은 레벨의 선택은 해제한다 (컬럼 드릴다운 규칙)."""
    s = sel()
    s[level] = node_id
    for lv in range(level + 1, schema.LEVEL_MAX + 1):
        s[lv] = None


def selected_node() -> dict | None:
    """가장 깊은 선택 노드 (상세 편집 대상)."""
    nmap = schema.node_map(data()["nodes"])
    s = sel()
    for lv in range(schema.LEVEL_MAX, schema.LEVEL_MIN - 1, -1):
        nid = s.get(lv)
        if nid and nid in nmap:
            return nmap[nid]
    return None


def select_path_to(node_id: str) -> None:
    """노드까지의 경로를 따라 각 레벨 선택을 맞춘다 (엑셀 업로드·복원 후 사용)."""
    nmap = schema.node_map(data()["nodes"])
    n = nmap.get(node_id)
    if n is None:
        return
    chain = schema.ancestors(nmap, node_id) + [n]
    s = sel()
    for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1):
        s[lv] = None
    for node in chain:
        s[node["level"]] = node["id"]


def prune_selection() -> None:
    """사라진 노드를 가리키는 선택 정리 (삭제·복원·업로드 후)."""
    nmap = schema.node_map(data()["nodes"])
    s = sel()
    for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1):
        nid = s.get(lv)
        if nid and nid not in nmap:
            for deeper in range(lv, schema.LEVEL_MAX + 1):
                s[deeper] = None
            break
        # 부모 선택과 어긋난 자식 선택도 정리
        if nid and lv > schema.LEVEL_MIN and nmap[nid].get("parent_id") != s.get(lv - 1):
            for deeper in range(lv, schema.LEVEL_MAX + 1):
                s[deeper] = None
            break


def set_flash(msg: str, kind: str = "success") -> None:
    """리런 후 1회만 보여줄 메시지."""
    st.session_state["_flash"] = (kind, msg)


def show_flash() -> None:
    f: Any = st.session_state.pop("_flash", None)
    if not f:
        return
    kind, msg = f
    {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}.get(kind, st.info)(msg)
