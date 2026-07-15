"""history_view.py — 스냅샷 이력 / 복원 / 감사로그.

스냅샷은 pre-image(덮어쓰기 직전의 디스크본)다. 누가 강제로 덮어썼더라도 직전 상태가
남아 있으므로 되살릴 수 있다. 복원도 정식 저장 경로를 타므로 이력에 다시 남는다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import schema
import state
import store
from pii import mask_name


def render() -> None:
    st.markdown("### 이력 · 복원")
    hist = store.list_history()
    if not hist:
        st.info("아직 저장 이력이 없습니다. 저장을 하면 직전 버전이 여기에 쌓입니다.")
        return

    st.caption("저장할 때마다 **직전 버전**이 스냅샷으로 보관됩니다 (최근 90일 + 최신 50개 유지).")
    df = pd.DataFrame([{"시각": h["ts"], "작성자": mask_name(h["author"]), "rev": h["rev"],
                        "업무 수": h["n_nodes"], "파일": h["file"]} for h in hist])
    st.dataframe(df.drop(columns=["파일"]), hide_index=True, use_container_width=True, height=280)

    labels = [f"{h['ts']} · {mask_name(h['author'])} · rev{h['rev']} · {h['n_nodes']}개" for h in hist]
    pick = st.selectbox("복원할 시점", labels, index=0)
    target = hist[labels.index(pick)]

    snap = store.load_snapshot(target["file"])
    if snap is None:
        st.error("이 스냅샷을 읽을 수 없습니다.")
        return

    cur = state.data()
    d = schema.diff(cur, snap)
    st.markdown("#### 지금 화면과의 차이 (복원하면 이렇게 바뀝니다)")
    m = st.columns(3)
    m[0].metric("되살아남", len(d["added"]))
    m[1].metric("값이 바뀜", len(d["changed"]))
    m[2].metric("사라짐", len(d["removed"]))

    nmap = schema.node_map(cur["nodes"])
    if d["removed"]:
        with st.expander(f"복원하면 사라질 업무 {len(d['removed'])}개"):
            st.dataframe(pd.DataFrame([
                {"레벨": f"lv{n.get('level', '')}",
                 "경로": " › ".join(schema.path_names(nmap, n["id"])[3:]) or n.get("name", "")}
                for n in d["removed"][:200]]), hide_index=True, use_container_width=True)

    if state.dirty():
        st.warning(f"저장하지 않은 변경 {state.dirty()}건이 있습니다. 복원하면 사라집니다.")

    ok = st.checkbox("위 내용을 확인했고 이 시점으로 되돌립니다", key="restore_ok")
    author = state.author()
    if not author:
        st.info("복원하려면 사이드바에 작성자를 입력하세요.")
    if st.button("이 시점으로 복원", type="primary", disabled=not (ok and author)):
        res, restored = store.restore(target["file"], author)
        if res.ok and restored is not None:
            st.session_state["data"] = restored
            state.mark_saved()
            state.prune_selection()
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            state.set_flash(f"{target['ts']} 시점으로 복원했습니다 (rev {res.rev}).")
            st.rerun()
        else:
            st.error(res.error or "복원에 실패했습니다.")

    st.divider()
    st.markdown("#### 저장 기록")
    aud = store.read_audit(100)
    if not aud:
        st.caption("기록이 없습니다.")
        return
    ACT = {"save": "저장", "force": "강제 덮어쓰기", "restore": "복원"}
    st.dataframe(pd.DataFrame([{
        "시각": a.get("ts", "").replace("T", " "),
        "작성자": mask_name(a.get("author", "")),
        "동작": ACT.get(a.get("action", ""), a.get("action", "")),
        "rev": a.get("rev", ""),
        "업무 수": a.get("n_nodes", ""),
    } for a in aud]), hide_index=True, use_container_width=True, height=260)
