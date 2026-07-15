"""tree_view.py — 계층 편집 화면 (컬럼 드릴다운 보드 + 카드 상세 편집).

보드는 두 경로가 있고 **데이터 조작 함수는 완전히 공유**한다:
  · DnD 경로   — dnd_component (SortableJS 커스텀 컴포넌트)
  · 간단 모드   — st.columns + ▲▼ 버튼 (컴포넌트 없이도 기능 100% 동등)

PII: 카드 payload 에는 마스킹된 담당자만 싣는다 (원본 이름이 iframe DOM 에 존재하지 않음).
상세 편집 입력 위젯에만 원본을 넣는다 — 마스킹하면 편집이 불가능해지기 때문.
"""
from __future__ import annotations

import streamlit as st

import dnd_component as dnd
import schema
import state
from pii import mask_name


def _fixed_breadcrumb(data: dict) -> None:
    """lv0~lv2 고정 계층 + 현재 선택 경로."""
    nmap = schema.node_map(data["nodes"])
    chips = [f'<span class="chip lock">{n}</span>' for n in schema.FIXED_LEVELS]
    s = state.sel()
    for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1):
        nid = s.get(lv)
        if nid and nid in nmap:
            chips.append(f'<span class="chip cur">{_esc(nmap[nid]["name"])}</span>')
    st.markdown('<div class="pd-fixed">' + '<span class="sep">›</span>'.join(chips) + "</div>",
                unsafe_allow_html=True)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _parent_for(level: int) -> str | None:
    """해당 레벨 컬럼이 보여줄 부모 id. None 이면 상위 선택이 안 된 상태."""
    if level == schema.LEVEL_MIN:
        return schema.ROOT_ID
    return state.sel().get(level - 1)


def build_columns(data: dict, query: str = "") -> list[dict]:
    """보드 컬럼 데이터 (DnD 컴포넌트 args / 간단 모드 공용).

    보이는 컬럼의 노드만 담는다 — 전체 트리를 넘기면 노드 수천 개에서 렌더가 무너진다.
    """
    idx = schema.children_index(data["nodes"])
    cnt = {pid: len(v) for pid, v in idx.items()}
    s = state.sel()
    q = query.strip().lower()
    cols: list[dict] = []
    for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1):
        pid = _parent_for(lv)
        cards: list[dict] = []
        if pid:
            for n in idx.get(pid, []):
                cards.append({
                    "id": n["id"],
                    "name": n.get("name", ""),
                    "ai": bool(n.get("has_ai_agent")),
                    "tech": list(n.get("tech") or [])[:2],
                    "tech_more": max(0, len(n.get("tech") or []) - 2),
                    "dept": n.get("dept", ""),
                    "owner": mask_name(n.get("owner", "")),     # ★ 원본 이름은 iframe 으로 안 나간다
                    "kids": cnt.get(n["id"], 0),
                    "hit": bool(q) and q in n.get("name", "").lower(),
                })
        cols.append({
            "level": lv,
            "label": f"lv{lv} {schema.LEVEL_LABELS.get(lv, '')}",
            "parent_id": pid,
            "enabled": pid is not None,
            "selected": s.get(lv),
            "cards": cards,
        })
    return cols


# ── 이벤트 적용 (DnD·간단모드 공용) ─────────────────────

def apply_event(evt: dict) -> bool:
    """컴포넌트 이벤트를 세션 트리에 반영. 화면 갱신이 필요하면 True."""
    data = state.data()
    author = state.author() or "unknown"
    t = evt.get("type")

    if t == "select":
        nid = evt.get("node_id")
        nmap = schema.node_map(data["nodes"])
        if nid in nmap:
            state.select(nmap[nid]["level"], nid)
        return True

    if t == "add":
        pid = evt.get("parent_id") or schema.ROOT_ID
        lv = int(evt.get("level") or schema.LEVEL_MIN)
        nid = schema.add_node(data, pid, lv, author)
        state.select(lv, nid)
        state.touch()
        return True

    if t == "reorder":
        pid = evt.get("parent_id") or schema.ROOT_ID
        order = [str(x) for x in (evt.get("order") or [])]
        if schema.apply_reorder(data, pid, order, author):
            state.touch()
        return True

    if t == "move":
        nid = str(evt.get("node_id") or "")
        new_pid = str(evt.get("new_parent_id") or schema.ROOT_ID)
        ok, msg = schema.apply_move(data, nid, new_pid, author)
        if ok:
            state.touch()
            state.prune_selection()
        elif msg:
            state.set_flash(msg, "warning")
        return True

    return False


# ── 간단 모드 보드 ──────────────────────────────────────

def _fallback_board(data: dict, cols: list[dict]) -> None:
    st.caption("간단 모드 — 카드 이름을 눌러 선택하고, ▲▼ 로 순서를 바꿉니다. "
               "부모 변경은 아래 상세 패널의 [옮기기] 를 쓰세요.")
    ui_cols = st.columns(len(cols), gap="small")
    for c, ui in zip(cols, ui_cols):
        with ui:
            st.markdown(f'<div class="pd-colhead">{_esc(c["label"])} '
                        f'({len(c["cards"])})</div>', unsafe_allow_html=True)
            if not c["enabled"]:
                st.markdown('<div class="pd-empty">상위 항목을 먼저 선택하세요.</div>',
                            unsafe_allow_html=True)
                continue
            if st.button(f"＋ 추가", key=f"add_{c['level']}", use_container_width=True):
                apply_event({"type": "add", "parent_id": c["parent_id"], "level": c["level"]})
                st.rerun()
            if not c["cards"]:
                st.markdown('<div class="pd-empty">항목이 없습니다.</div>', unsafe_allow_html=True)
                continue
            for i, card in enumerate(c["cards"]):
                b, up, dn = st.columns([6, 1, 1], gap="small")
                with b:
                    label = card["name"] + ("  🤖" if card["ai"] else "")
                    if card["kids"]:
                        label += f"  ({card['kids']})"
                    if st.button(label, key=f"sel_{card['id']}", use_container_width=True,
                                 type="primary" if c["selected"] == card["id"] else "secondary"):
                        apply_event({"type": "select", "node_id": card["id"]})
                        st.rerun()
                with up:
                    if st.button("▲", key=f"up_{card['id']}", disabled=(i == 0)):
                        if schema.move_sibling(data, card["id"], -1, state.author() or "unknown"):
                            state.touch()
                        st.rerun()
                with dn:
                    if st.button("▼", key=f"dn_{card['id']}", disabled=(i == len(c["cards"]) - 1)):
                        if schema.move_sibling(data, card["id"], 1, state.author() or "unknown"):
                            state.touch()
                        st.rerun()


# ── 상세 편집 ───────────────────────────────────────────

def _move_panel(data: dict, node: dict) -> None:
    """부모 변경 — DnD 로 불가능한 유일한 조작(레벨 변경 포함)이라 여기서만 제공."""
    nmap = schema.node_map(data["nodes"])
    idx = schema.children_index(data["nodes"])
    banned = {node["id"]} | {d["id"] for d in schema.descendants(idx, node["id"])}
    depth_below = schema.max_depth_below(data, node["id"])

    opts: list[tuple[str, str]] = [(schema.ROOT_ID, f"(최상위) lv{schema.LEVEL_MIN} 로 이동")]
    for n in sorted(data["nodes"], key=lambda x: (x["level"], x["name"])):
        if n["id"] in banned:
            continue
        if n["level"] + 1 + depth_below > schema.LEVEL_MAX:
            continue        # 옮기면 lv6 을 넘는 대상은 아예 목록에서 뺀다
        path = " › ".join(a["name"] for a in schema.ancestors(nmap, n["id"]))
        label = f"lv{n['level'] + 1} ← {path + ' › ' if path else ''}{n['name']}"
        opts.append((n["id"], label))

    if len(opts) == 1 and node["parent_id"] == schema.ROOT_ID:
        st.caption("옮길 수 있는 상위 업무가 없습니다.")
        return

    cur = node["parent_id"]
    labels = [o[1] for o in opts]
    ids = [o[0] for o in opts]
    default = ids.index(cur) if cur in ids else 0
    pick = st.selectbox("상위 업무 (옮기기)", labels, index=default, key=f"mv_{node['id']}")
    new_pid = ids[labels.index(pick)]
    if depth_below:
        st.caption(f"하위 업무 {len(schema.descendants(idx, node['id']))}개도 함께 이동하며 레벨이 다시 매겨집니다.")
    if st.button("옮기기", key=f"mvbtn_{node['id']}", disabled=(new_pid == cur)):
        ok, msg = schema.apply_move(data, node["id"], new_pid, state.author() or "unknown")
        if ok:
            state.touch()
            state.select_path_to(node["id"])
            state.set_flash(f"'{node['name']}' 을(를) 옮겼습니다.")
        else:
            state.set_flash(msg or "이동하지 않았습니다.", "warning")
        st.rerun()


def _detail(data: dict, node: dict) -> None:
    doms = data.get("domains", {})
    nmap = schema.node_map(data["nodes"])
    path = " › ".join(schema.path_names(nmap, node["id"]))
    st.markdown(f"#### 상세 · lv{node['level']} {schema.LEVEL_LABELS.get(node['level'], '')}")
    st.caption(path)

    def _opts(key: str, extra: str | list | None = None) -> list[str]:
        """도메인 목록 + 현재 값(목록에 없어도 유실되지 않게)."""
        base = list(doms.get(key, []))
        vals = extra if isinstance(extra, list) else ([extra] if extra else [])
        for v in vals:
            if v and v not in base:
                base.append(v)
        return base

    with st.form(f"detail_{node['id']}"):
        name = st.text_input("업무명", value=node.get("name", ""))
        desc = st.text_area("업무 설명", value=node.get("desc", ""), height=90)

        c1, c2 = st.columns(2)
        with c1:
            dept_opts = [""] + _opts("dept", node.get("dept"))
            dept = st.selectbox("부서/과", dept_opts,
                                index=dept_opts.index(node.get("dept", "")) if node.get("dept", "") in dept_opts else 0)
            owner = st.text_input("담당자", value=node.get("owner", ""),
                                  help="저장 데이터에는 원본을 보관하고 카드·엑셀 표시에서만 마스킹합니다.")
            freq_opts = [""] + _opts("frequency", node.get("frequency"))
            freq = st.selectbox("수행 주기", freq_opts,
                                index=freq_opts.index(node.get("frequency", "")) if node.get("frequency", "") in freq_opts else 0)
        with c2:
            has_ai = st.checkbox("AI 에이전트 적용", value=bool(node.get("has_ai_agent")))
            tech = st.multiselect("활용 기술", _opts("tech", list(node.get("tech") or [])),
                                  default=list(node.get("tech") or []))
            auto_opts = [""] + _opts("automation_level", node.get("automation_level"))
            auto = st.selectbox("자동화 수준", auto_opts,
                                index=auto_opts.index(node.get("automation_level", "")) if node.get("automation_level", "") in auto_opts else 0)

        outputs = st.text_input("산출물 / 연계 시스템", value=node.get("outputs", ""),
                                placeholder="예: 시운전 결과보고서 / SAP, TBM앱")

        new_tech = st.text_input("목록에 없는 기술 추가", placeholder="쉼표로 여러 개 입력 후 [적용]",
                                 help="입력한 기술은 이 업무에 적용되고 도메인 마스터에도 등록됩니다.")

        if st.form_submit_button("적용", type="primary"):
            if not name.strip():
                state.set_flash("업무명은 비울 수 없습니다.", "warning")
                st.rerun()
            extra = [t.strip() for t in (new_tech or "").split(",") if t.strip()]
            for t in extra:
                if t not in doms.setdefault("tech", []):
                    doms["tech"].append(t)
            schema.update_node(data, node["id"], {
                "name": name.strip(), "desc": desc, "dept": dept, "owner": owner,
                "frequency": freq, "has_ai_agent": has_ai,
                "tech": list(dict.fromkeys(list(tech) + extra)),
                "automation_level": auto, "outputs": outputs,
            }, state.author() or "unknown")
            state.touch()
            state.set_flash(f"'{name.strip()}' 을(를) 수정했습니다. 사이드바 [저장] 을 눌러야 파일에 반영됩니다.")
            st.rerun()

    with st.expander("옮기기 / 삭제"):
        _move_panel(data, node)
        st.divider()
        idx = schema.children_index(data["nodes"])
        kids = schema.descendants(idx, node["id"])
        if kids:
            st.warning(f"하위 업무 {len(kids)}개가 함께 삭제됩니다.")
            ok = st.checkbox(f"하위 {len(kids)}개까지 삭제에 동의합니다", key=f"delok_{node['id']}")
        else:
            ok = True
        if st.button("이 업무 삭제", key=f"del_{node['id']}", disabled=not ok):
            nm = node["name"]
            removed = schema.delete_node(data, node["id"])
            state.touch()
            state.prune_selection()
            state.set_flash(f"'{nm}' 외 {removed - 1}개 하위 업무를 삭제했습니다." if removed > 1
                            else f"'{nm}' 을(를) 삭제했습니다.")
            st.rerun()


# ── 진입 ────────────────────────────────────────────────

def render() -> None:
    data = state.data()
    state.prune_selection()

    top = st.columns([3, 2])
    with top[0]:
        _fixed_breadcrumb(data)
    with top[1]:
        query = st.text_input("업무명 검색", key="q", placeholder="이름으로 찾기",
                              label_visibility="collapsed")

    cols = build_columns(data, query or "")
    use_dnd = st.session_state.get("dnd_enabled", True) and dnd.is_available()

    if use_dnd:
        try:
            raw = dnd.render_board(cols, key="pd_board")
            evt = dnd.take_event(raw)
            if evt and apply_event(evt):
                st.rerun()
        except Exception as e:      # 컴포넌트가 죽어도 앱은 살아야 한다 (공통규칙 5)
            st.session_state["dnd_error"] = str(e)
            st.session_state["dnd_enabled"] = False
            st.warning(f"드래그앤드롭 화면을 띄우지 못해 간단 모드로 전환했습니다. ({e})")
            _fallback_board(data, cols)
    else:
        _fallback_board(data, cols)

    st.divider()
    node = state.selected_node()
    if node is None:
        st.info("카드를 선택하면 상세 내용을 편집할 수 있습니다.")
    else:
        _detail(data, node)
