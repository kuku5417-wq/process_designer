"""domain_view.py — 도메인 마스터 관리 (부서/과 · 활용기술 · 자동화수준 · 수행주기).

사용 중인 값은 삭제를 차단하고, 이름 변경은 노드 값 일괄 치환으로 따라간다.
그렇지 않으면 카드가 목록에 없는 값을 가리키는 유령 상태가 된다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import schema
import state

_LIST_FIELDS = {"tech"}       # 노드에서 list 로 들고 있는 도메인


def _usage(data: dict, key: str) -> dict[str, int]:
    """도메인 값별 사용 노드 수."""
    cnt: dict[str, int] = {}
    for n in data["nodes"]:
        if key in _LIST_FIELDS:
            for v in n.get(key) or []:
                cnt[v] = cnt.get(v, 0) + 1
        else:
            v = n.get(key)
            if v:
                cnt[v] = cnt.get(v, 0) + 1
    return cnt


def _rename_in_nodes(data: dict, key: str, old: str, new: str) -> int:
    n_changed = 0
    for n in data["nodes"]:
        if key in _LIST_FIELDS:
            vals = n.get(key) or []
            if old in vals:
                n[key] = [new if v == old else v for v in vals]
                n_changed += 1
        elif n.get(key) == old:
            n[key] = new
            n_changed += 1
    return n_changed


def _render_one(data: dict, key: str) -> None:
    label = schema.DOMAIN_LABELS.get(key, key)
    doms = data.setdefault("domains", {})
    cur: list[str] = list(doms.get(key, []))
    use = _usage(data, key)

    st.caption(f"행을 추가·수정·삭제한 뒤 [적용] 을 누르세요. 사용 중인 값은 삭제할 수 없습니다.")
    df = pd.DataFrame({"값": cur, "사용 중": [use.get(v, 0) for v in cur]})
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "값": st.column_config.TextColumn("값", required=True),
            "사용 중": st.column_config.NumberColumn("사용 중", disabled=True, help="이 값을 쓰는 업무 수"),
        },
        key=f"de_{key}",
    )

    if st.button("적용", key=f"apply_{key}", type="primary"):
        new_vals = [str(v).strip() for v in edited["값"].tolist() if str(v).strip()]
        if len(set(new_vals)) != len(new_vals):
            state.set_flash("중복된 값이 있습니다.", "warning")
            st.rerun()

        # 위치 기준 매칭으로 rename 추출 (같은 행의 값이 바뀐 경우)
        renames: list[tuple[str, str]] = []
        for i, old in enumerate(cur):
            if i < len(new_vals) and new_vals[i] != old:
                renames.append((old, new_vals[i]))

        renamed_set = {o for o, _ in renames}
        removed = [v for v in cur if v not in new_vals and v not in renamed_set]
        blocked = [v for v in removed if use.get(v, 0) > 0]
        if blocked:
            state.set_flash("사용 중이라 삭제할 수 없습니다: "
                            + ", ".join(f"{v}({use[v]}개 업무)" for v in blocked), "warning")
            st.rerun()

        n_touched = 0
        for old, new in renames:
            n_touched += _rename_in_nodes(data, key, old, new)
        doms[key] = new_vals
        state.touch()
        msg = f"{label} 목록을 적용했습니다."
        if n_touched:
            msg += f" 이름 변경에 따라 업무 {n_touched}개의 값도 함께 바꿨습니다."
        state.set_flash(msg + " 사이드바 [저장] 을 눌러야 파일에 반영됩니다.")
        st.rerun()


def render() -> None:
    data = state.data()
    st.markdown("### 도메인 관리")
    st.caption("카드 편집에서 고를 수 있는 선택 목록입니다. 활용기술은 카드 상세에서 즉석 추가도 됩니다.")
    keys = list(schema.DOMAIN_LABELS.keys())
    tabs = st.tabs([schema.DOMAIN_LABELS[k] for k in keys])
    for k, t in zip(keys, tabs):
        with t:
            _render_one(data, k)
