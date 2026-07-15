"""app.py — 시운전팀 프로세스 설계 (포트 8540).

lv0 조선 › lv1 생산 › lv2 시운전 (고정) 아래로 lv3~lv6 업무 계층을 카드로 설계하고,
업무별 AI 에이전트 적용 여부·활용 기술·담당 부서를 기록한다. 공용PC에 띄워 여러 명이
함께 편집하며, 저장 시 작성자와 스냅샷이 남아 언제든 되돌릴 수 있다.

데이터는 <base>/process/process_tree.json (parquet 아님 — 공통규칙 3).
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import path_config as pc            # noqa: E402
import state                        # noqa: E402
import store                        # noqa: E402
import dnd_component as dnd         # noqa: E402
from pii import mask_name           # noqa: E402
from ui_styles import inject_css    # noqa: E402
from views import domain_view, excel_view, history_view, tree_view   # noqa: E402

st.set_page_config(page_title="프로세스 설계", page_icon="🗂️", layout="wide",
                   initial_sidebar_state="expanded")
inject_css()
state.init()

MENUS = ["계층 편집", "도메인 관리", "엑셀 가져오기 / 내보내기", "이력 · 복원"]


def _check_disk_changed() -> None:
    """다른 사람이 저장했는지 감지 (stat 1회 — esg/app.py 의 센티널 패턴).

    첫 진입 시에는 배너를 띄우지 않는다 (내가 방금 읽은 것이 최신이므로).
    """
    mtime, rev, author = store.disk_stat()
    seen = st.session_state.get("disk_seen_mtime", 0.0)
    if seen == 0.0:
        st.session_state["disk_seen_mtime"] = mtime
        return
    if mtime > seen:
        st.session_state["disk_newer"] = (rev, author)


def _save(force: bool = False) -> None:
    res = store.save_tree(state.data(), state.author(), force=force)
    if res.ok:
        state.mark_saved()
        st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
        st.session_state.pop("conflict", None)
        st.session_state.pop("disk_newer", None)
        state.set_flash(f"저장했습니다 (rev {res.rev}).")
        st.rerun()
    elif res.conflict:
        st.session_state["conflict"] = res
        st.rerun()
    else:
        state.set_flash(res.error or "저장에 실패했습니다.", "error")
        st.rerun()


def _sidebar() -> None:
    with st.sidebar:
        st.markdown('<div class="pd-brand">🗂️ 프로세스 설계'
                    '<span>조선소 시운전팀</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="pd-env">{pc.get_env_label()}</div>', unsafe_allow_html=True)

        st.text_input("작성자", key="author", placeholder="이름을 입력하세요",
                      help="저장 기록과 스냅샷에 남습니다. 화면 표시는 마스킹됩니다.")
        author = state.author()

        n = state.dirty()
        if n:
            st.markdown(f'<div class="pd-dirty">● 저장하지 않은 변경 {n}건</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="pd-clean">변경 없음</div>', unsafe_allow_html=True)

        if st.button("💾 저장", type="primary", disabled=not (author and n),
                     help="작성자를 입력하고 변경이 있어야 저장할 수 있습니다."):
            _save()
        if not author and n:
            st.caption("작성자를 입력하면 저장할 수 있습니다.")

        _, rev, last_author = store.disk_stat()
        if rev:
            st.caption(f"마지막 저장 · rev {rev} · {mask_name(last_author) or '-'}")

        if st.button("↻ 디스크 다시 읽기", help="다른 사람이 저장한 최신 내용을 불러옵니다."):
            if state.dirty():
                st.session_state["confirm_reload"] = True
                st.rerun()
            warns = state.reload_from_disk()
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state.pop("disk_newer", None)
            state.set_flash("최신 내용을 불러왔습니다." + (" / ".join(warns) if warns else ""))
            st.rerun()

        st.divider()
        st.radio("화면", MENUS, key="menu", label_visibility="collapsed")

        st.divider()
        if dnd.is_available():
            st.toggle("드래그앤드롭 사용", key="dnd_enabled",
                      help="끄면 버튼으로 순서를 바꾸는 간단 모드가 됩니다.")
        else:
            miss = dnd.missing_assets()
            st.session_state["dnd_enabled"] = False
            st.caption("간단 모드 (드래그앤드롭 자산 없음"
                       + (f": {', '.join(miss)}" if miss else ": pyarrow 미설치") + ")")


def _conflict_dialog() -> bool:
    """저장 충돌 안내. True 면 아래 화면을 그리지 않는다."""
    res = st.session_state.get("conflict")
    if not res:
        return False
    st.error(f"**{mask_name(res.disk_author) or '다른 사람'}** 님이 먼저 저장했습니다 "
             f"(rev {res.disk_rev}, {res.disk_updated_at}). 내 편집본은 rev {res.rev} 기준입니다.")
    st.caption("자동 병합은 하지 않습니다. 덮어쓰더라도 상대 버전은 이력에 남아 복원할 수 있습니다.")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("최신 내용 불러오기 (내 변경 버림)", use_container_width=True):
            state.reload_from_disk()
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state.pop("conflict", None)
            st.session_state.pop("disk_newer", None)
            state.set_flash("최신 내용을 불러왔습니다.")
            st.rerun()
    with c2:
        if st.button("내 것으로 덮어쓰기", type="primary", use_container_width=True):
            _save(force=True)
    with c3:
        if st.button("취소", use_container_width=True):
            st.session_state.pop("conflict", None)
            st.rerun()
    return True


def _reload_confirm() -> bool:
    if not st.session_state.get("confirm_reload"):
        return False
    st.warning(f"저장하지 않은 변경 {state.dirty()}건이 있습니다. 다시 읽으면 사라집니다.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("그래도 다시 읽기", type="primary", use_container_width=True):
            state.reload_from_disk()
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state.pop("confirm_reload", None)
            st.session_state.pop("disk_newer", None)
            state.set_flash("최신 내용을 불러왔습니다.")
            st.rerun()
    with c2:
        if st.button("취소", use_container_width=True):
            st.session_state.pop("confirm_reload", None)
            st.rerun()
    return True


def main() -> None:
    _check_disk_changed()
    _sidebar()

    for w in st.session_state.pop("load_warns", []) or []:
        st.warning(w)
    state.show_flash()

    newer = st.session_state.get("disk_newer")
    if newer and not st.session_state.get("conflict"):
        rev, author = newer
        st.info(f"**{mask_name(author) or '다른 사람'}** 님이 방금 저장했습니다 (rev {rev}). "
                "사이드바 [↻ 디스크 다시 읽기] 로 최신 내용을 받을 수 있습니다.")

    if _conflict_dialog() or _reload_confirm():
        return

    # 라벨이 아니라 MENUS 위치로 라우팅 — 라벨 문구를 고칠 때 라우터가 조용히 어긋나지 않게
    menu = st.session_state.get("menu", MENUS[0])
    renderer = dict(zip(MENUS, (tree_view.render, domain_view.render,
                                excel_view.render, history_view.render)))
    renderer.get(menu, tree_view.render)()


main()
