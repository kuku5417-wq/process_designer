"""excel_view.py — 엑셀 가져오기(일괄등록) / 내보내기 + JSON 백업.

화면을 [가져오기][내보내기] 탭으로 나눈다 — 업로드가 다운로드 아래에 묻혀 있으면 기능이
있는 줄도 모른다(실제로 그런 일이 있었다). 가져오기를 먼저 둔다.

업로드는 반영 전에 추가·변경·삭제 건수를 보여주고 확인을 받는다. 삭제는 기본 OFF —
엑셀에서 행을 지웠다는 이유만으로 업무가 사라지면 사고이기 때문에 명시적 옵트인으로 둔다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import excel_io
import schema
import state


@st.cache_data(show_spinner=False)
def _xlsx_cached(rev: int, dirty: int, mask: bool, _data: dict) -> bytes:
    """(rev, dirty, mask) 키 캐시 — 리런마다 xlsx 를 재빌드하지 않게."""
    return excel_io.build_xlsx(_data, mask=mask)


def _download(data: dict) -> None:
    st.caption("설계 결과를 엑셀로 받습니다. 이 파일을 고쳐서 [가져오기] 탭에 올리면 일괄 반영됩니다.")
    mask = st.checkbox("담당자 이름 마스킹 (홍길동 → 홍*동)", value=True,
                       help="공유·보고용은 켜두세요. 끄면 원본 이름이 파일에 그대로 들어갑니다.")
    if not mask:
        st.warning("마스킹을 끄면 개인정보가 담긴 파일이 됩니다. 외부 공유에 주의하세요.")
    try:
        xb = _xlsx_cached(int(data.get("rev", 0)), state.dirty(), mask, data)
    except Exception as e:
        st.error(f"엑셀 생성에 실패했습니다: {e}")
        return
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("엑셀 다운로드 (.xlsx)", data=xb, file_name=excel_io.default_filename(),
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", use_container_width=True)
    with c2:
        st.download_button("JSON 원본 다운로드", data=excel_io.build_json_bytes(data),
                           file_name=excel_io.default_filename(ext="json"),
                           mime="application/json", use_container_width=True)
    st.caption("시트 3장 — 계층도(업무 1개=1행, lv0~lv6 펼침) / 도메인 / 요약.")


def _preview_table(nodes: list[dict], data: dict) -> pd.DataFrame:
    nmap = schema.node_map(data["nodes"])
    rows = []
    for n in nodes[:200]:
        path = " › ".join(schema.path_names(nmap, n["id"])[3:]) or n.get("name", "")
        rows.append({"레벨": f"lv{n.get('level', '')}", "경로": path, "부서/과": n.get("dept", "")})
    return pd.DataFrame(rows)


def _upload(data: dict) -> None:
    st.caption("엑셀로 업무를 한 번에 등록·수정합니다. **[내보내기] 탭에서 받은 파일**을 고쳐서 "
               "올리세요 — 컬럼 서식이 같아야 읽을 수 있습니다.")
    up = st.file_uploader("수정한 엑셀 파일 (.xlsx)", type=["xlsx"], key="up_xlsx")
    if up is None:
        st.session_state.pop("up_parsed", None)
        with st.expander("엑셀 작성 규칙"):
            st.markdown(
                "- `lv3`~`lv6` 칸을 **왼쪽부터 연속으로** 채웁니다. (lv4 를 비우고 lv5 만 채우면 오류)\n"
                "- 상위 업무는 **자기 행이 반드시 있어야** 합니다.\n"
                "- `id` 칸이 비어 있으면 **새 업무**로 등록됩니다. 기존 업무는 `id` 를 지우지 마세요.\n"
                f"- AI·기술·부서 같은 세부 정보는 **lv{schema.FULL_DETAIL_LEVEL} "
                f"{schema.LEVEL_LABELS[schema.FULL_DETAIL_LEVEL]}** 행에만 채웁니다.\n"
                "- 엑셀에서 행을 지워도 **기본은 삭제되지 않습니다** (실수 방지). 삭제하려면 아래 "
                "체크박스를 켜세요.\n"
                "- 담당자가 마스킹된 값(`홍*동`)이면 원본 이름을 덮어쓰지 않습니다.")
        return

    try:
        parsed, errs = excel_io.parse_excel(up.getvalue(), data)
    except Exception as e:
        st.error(f"엑셀을 읽을 수 없습니다: {e}")
        return

    if errs:
        st.error("엑셀에 문제가 있습니다. 고친 뒤 다시 올려주세요.")
        for e in errs[:20]:
            st.write(f"- {e}")
        if len(errs) > 20:
            st.caption(f"…외 {len(errs) - 20}건")
        return

    d = schema.diff(data, parsed)
    del_on = st.checkbox(f"엑셀에 없는 업무 {len(d['removed'])}개를 삭제합니다", value=False,
                         disabled=not d["removed"],
                         help="기본은 삭제하지 않습니다. 엑셀에서 행을 지운 것이 실수일 수 있기 때문입니다.")

    final = parsed
    if d["removed"] and not del_on:
        # 삭제 대상을 되살려 병합 — 부모가 사라졌으면 normalize 가 ROOT 로 구제한다
        final = {**parsed, "nodes": list(parsed["nodes"]) + [dict(n) for n in d["removed"]]}
        final = schema.normalize(final)
        d = schema.diff(data, final)

    m1, m2, m3 = st.columns(3)
    m1.metric("추가", len(d["added"]))
    m2.metric("변경", len(d["changed"]))
    m3.metric("삭제", len(d["removed"]))

    if d["added"]:
        with st.expander(f"추가될 업무 {len(d['added'])}개"):
            st.dataframe(_preview_table(d["added"], final), hide_index=True, use_container_width=True)
    if d["changed"]:
        with st.expander(f"변경될 업무 {len(d['changed'])}개"):
            st.dataframe(_preview_table(d["changed"], final), hide_index=True, use_container_width=True)
    if d["removed"]:
        with st.expander(f"삭제될 업무 {len(d['removed'])}개"):
            st.dataframe(_preview_table(d["removed"], data), hide_index=True, use_container_width=True)

    unknown = excel_io.unknown_domain_values(final)
    add_dom = False
    if unknown:
        st.info("도메인 목록에 없는 값이 있습니다: "
                + " / ".join(f"{schema.DOMAIN_LABELS.get(k, k)}: {', '.join(v)}" for k, v in unknown.items()))
        add_dom = st.checkbox("이 값들을 도메인 목록에 추가", value=True)

    if not (d["added"] or d["changed"] or d["removed"] or (unknown and add_dom)):
        st.success("현재 데이터와 차이가 없습니다.")
        return

    if st.button("이 내용으로 반영", type="primary"):
        if add_dom:
            doms = final.setdefault("domains", {})
            for k, vals in unknown.items():
                for v in vals:
                    if v not in doms.setdefault(k, []):
                        doms[k].append(v)
        final["rev"] = data.get("rev", 0)
        st.session_state["data"] = final
        state.touch()
        state.prune_selection()
        state.set_flash(f"엑셀을 반영했습니다 (추가 {len(d['added'])} · 변경 {len(d['changed'])} · "
                        f"삭제 {len(d['removed'])}). 사이드바 [저장] 을 눌러야 파일에 반영됩니다.")
        st.rerun()


def _summary(data: dict) -> None:
    s = schema.stats(data)
    lv6 = f"lv{schema.FULL_DETAIL_LEVEL} {schema.LEVEL_LABELS[schema.FULL_DETAIL_LEVEL]}"
    st.markdown("#### 요약")
    st.caption(f"AI 에이전트·부서·자동화 지표는 **{lv6}** 만 셉니다. "
               "상위 레벨(부문·대분류·중분류)은 업무를 묶는 분류라 상세 정보를 입력하지 않습니다.")
    c = st.columns(4)
    c[0].metric("전체 업무", s["total"], help="lv3~lv6 전 레벨 노드 수")
    c[1].metric(f"{lv6}", s["detail_total"], help="AI 지표의 분모")
    pct = f"{s['ai_yes'] / s['detail_total'] * 100:.0f}%" if s["detail_total"] else None
    c[2].metric("AI 에이전트 적용", s["ai_yes"], delta=pct, delta_color="off",
                help=f"{lv6} {s['detail_total']}개 중")
    c[3].metric("미적용", s["ai_no"], help=f"{lv6} 중 AI 에이전트가 없는 업무")
    lv = pd.DataFrame([{"레벨": f"lv{k} {schema.LEVEL_LABELS.get(k, '')}", "업무 수": v}
                       for k, v in s["by_level"].items()])
    d1, d2 = st.columns(2)
    with d1:
        st.dataframe(lv, hide_index=True, use_container_width=True)
    with d2:
        st.dataframe(pd.DataFrame([{"부서/과": k, f"{lv6} 수": v} for k, v in s["by_dept"].items()]),
                     hide_index=True, use_container_width=True)


def render() -> None:
    data = state.data()
    st.markdown("### 엑셀 가져오기 / 내보내기")
    if state.dirty():
        st.warning(f"저장하지 않은 변경 {state.dirty()}건이 있습니다. 내보내기 파일에는 포함되지만 "
                   "다른 사람은 아직 볼 수 없습니다.")
    # 가져오기를 먼저 — 다운로드 아래에 묻히면 업로드가 있는 줄 모른다
    t_up, t_down = st.tabs(["⬆ 가져오기 (일괄등록)", "⬇ 내보내기"])
    with t_up:
        _upload(data)
    with t_down:
        _download(data)
    st.divider()
    _summary(data)
