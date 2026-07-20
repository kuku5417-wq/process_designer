"""app.py — 시운전팀 프로세스 설계 (v2 · 단일 웹 컴포넌트 프론트엔드 호스트).

■ 구조 (기존 app.py 와의 차이)
  - 예전: Streamlit 위젯(사이드바·상세폼) + 컬럼 보드만 커스텀 컴포넌트, 컴포넌트는 "이벤트 1건"을 반환.
  - v2 : frontend/index.html 하나가 전체 UI·편집 상태를 브라우저에서 들고 있고,
          이 파일은 "데이터 저장 API" 역할만 한다. 컴포넌트는 [저장] 시 트리 전체를 되돌려주며,
          여기서 store.save_tree() 로 원자적 저장 + 스냅샷 + rev 충돌검사한다.

  ★ pyarrow 는 계속 필요하다 — components.v1 이 인스턴스 생성 시 import 하므로 없으면 죽는다.
  ★ 저장/스냅샷/이력/엑셀 로직(store.py, excel_io.py, schema.py)은 그대로 재사용한다.

  배치: 이 파일을 기존 app.py 자리(process_designer/ 루트)에 두고,
        frontend/index.html 을 v2 버전으로 교체한다. sortable.min.js 는 더 이상 필요 없다.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent))

import path_config as pc          # noqa: E402
import schema                     # noqa: E402
import store                      # noqa: E402
import excel_io                   # noqa: E402

st.set_page_config(page_title="프로세스 설계", page_icon="🗂️", layout="wide",
                   initial_sidebar_state="collapsed")

# 컴포넌트 iframe 이 화면 전체를 쓰도록 기본 패딩 제거
st.markdown("<style>.block-container{padding:0;max-width:100%}"
            "header[data-testid='stHeader']{display:none}</style>", unsafe_allow_html=True)

_FRONTEND = Path(__file__).resolve().parent / "frontend"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _component():
    if "_comp" not in st.session_state:
        # key_as_main_identity: name 고정. path 로 폴더를 그대로 서빙(npm 빌드 없음).
        st.session_state["_comp"] = components.declare_component("process_board_v2", path=str(_FRONTEND))
    return st.session_state["_comp"]


def _set_data(data: dict) -> None:
    """세션 트리 교체 + epoch 증가.

    프론트는 epoch 이 바뀔 때만 자기 트리를 파이썬 것으로 갈아끼운다. 그렇지 않으면
    download·histpick 처럼 단순 조회성 왕복에도 화면이 저장본으로 되돌아가
    **저장 안 한 편집이 조용히 사라진다**.
    """
    st.session_state["data"] = data
    st.session_state["tree_epoch"] = st.session_state.get("tree_epoch", 0) + 1


def _load() -> dict:
    if "data" not in st.session_state:
        data, warns = store.load_tree()
        _set_data(data)
        st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
        if warns:
            st.session_state["flash"] = " / ".join(warns)
    return st.session_state["data"]


def _args(flash: str, conflict, dirty_all: bool) -> dict:
    data = st.session_state["data"]
    mtime, rev, author = store.disk_stat()
    seen = st.session_state.get("disk_seen_mtime", 0.0)
    disk_newer = None
    if seen and mtime > seen and not conflict:
        disk_newer = {"rev": rev, "author": author}
    return {
        "tree": {"nodes": data.get("nodes", []),
                 "domains": data.get("domains", {}),
                 "rev": data.get("rev", 0)},
        # 프론트는 epoch 이 바뀔 때만 트리를 갈아끼운다 (미저장 편집 보호 — _set_data 주석 참조)
        "tree_epoch": st.session_state.get("tree_epoch", 0),
        "author": st.session_state.get("author", ""),
        "env": pc.get_env_label(),
        "history": store.list_history(),
        "audit": store.read_audit(100),
        "flash": flash,
        "conflict": conflict,
        "disk_newer": disk_newer,
        "dirty_all": dirty_all,
        # 판정은 파이썬이 한다 — 프론트는 결과만 그린다
        "diff_preview": st.session_state.get("diff_preview"),
        "import_preview": st.session_state.get("import_preview"),
        "import_errors": st.session_state.get("import_errors") or [],
    }


def _node_brief(nodes: list[dict], base: dict, limit: int = 200) -> list[dict]:
    """미리보기 목록용 — 레벨 + 경로 문자열 (lv0~lv2 고정단은 뺀다)."""
    nmap = schema.node_map(base.get("nodes", []))
    out = []
    for n in nodes[:limit]:
        path = " › ".join(schema.path_names(nmap, n["id"])[3:]) or n.get("name", "")
        out.append({"level": n.get("level", ""), "path": path})
    return out


def _preview_import(raw_bytes: bytes, filename: str = "") -> None:
    """올린 파일을 파싱만 하고 **보류**한다. 반영은 사용자가 확인한 뒤 import_apply 에서.

    확인 없이 반영하면 되돌릴 방법이 [디스크 다시 읽기] 뿐이라 위험하다 (v1 은 미리보기가 있었다).

    .json = 개인 배포판(standalone)이 내보낸 파일, .xlsx = 이 앱에서 받아 고친 엑셀.
    """
    data = st.session_state["data"]
    if filename.lower().endswith(".json") or raw_bytes[:1] == b"{":
        parsed, errs = excel_io.parse_json(raw_bytes, data)
    else:
        parsed, errs = excel_io.parse_excel(raw_bytes, data)
    if errs:
        st.session_state["import_errors"] = ([f"📄 {filename}"] if filename else []) + errs[:20]
        st.session_state.pop("pending_import", None)
        return
    st.session_state.pop("import_errors", None)
    d = schema.diff(data, parsed)
    st.session_state["pending_import"] = parsed
    st.session_state["import_preview"] = {
        "filename": filename,
        "added": len(d["added"]), "changed": len(d["changed"]), "removed": len(d["removed"]),
        "added_list": _node_brief(d["added"], parsed),
        "changed_list": _node_brief(d["changed"], parsed),
        "removed_list": _node_brief(d["removed"], data),
        "unknown": excel_io.unknown_domain_values(parsed),
        "labels": schema.DOMAIN_LABELS,
    }


def _apply_import(delete_missing: bool, add_domains: bool) -> None:
    parsed = st.session_state.get("pending_import")
    if not parsed:
        st.session_state["flash"] = "반영할 엑셀이 없습니다. 파일을 다시 올려주세요."
        return
    data = st.session_state["data"]
    d = schema.diff(data, parsed)
    if d["removed"] and not delete_missing:
        # 삭제 옵트인이 꺼져 있으면 사라진 노드를 되살려 병합 (엑셀 행 삭제 = 실수일 수 있다)
        parsed = schema.normalize({**parsed, "nodes": list(parsed["nodes"]) + [dict(n) for n in d["removed"]]})
        d = schema.diff(data, parsed)
    if add_domains:
        doms = parsed.setdefault("domains", {})
        for k, vals in excel_io.unknown_domain_values(parsed).items():
            for v in vals:
                if v not in doms.setdefault(k, []):
                    doms[k].append(v)
    parsed["rev"] = data.get("rev", 0)
    _set_data(parsed)
    st.session_state["dirty_all"] = True   # 반영분은 저장 전까지 '미저장'
    for k in ("pending_import", "import_preview", "import_errors"):
        st.session_state.pop(k, None)
    st.session_state["flash"] = (f"엑셀 반영: 추가 {len(d['added'])} · 변경 {len(d['changed'])} · "
                                 f"삭제 {len(d['removed'])}. 상단 [저장]을 눌러야 파일에 기록됩니다.")


def _preview_restore(name: str, nodes: list[dict] | None) -> None:
    """복원 미리보기 — 실제 계산은 검증된 schema.diff 가 한다 (JS 재구현 금지).

    비교 대상은 **화면의 현재 트리**(저장 안 한 편집 포함)다 — v1 history_view 와 동일.
    미리보기일 뿐이므로 session_state 의 데이터는 건드리지 않는다.
    """
    snap = store.load_snapshot(name)
    if snap is None:
        st.session_state["diff_preview"] = None
        return
    cur = st.session_state["data"]
    if nodes is not None:
        cur = {**cur, "nodes": nodes}      # 세션 원본 미변경 — 얕은 사본으로만 비교
    d = schema.diff(cur, snap)
    st.session_state["diff_preview"] = {
        "file": name,
        "added": len(d["added"]), "changed": len(d["changed"]), "removed": len(d["removed"]),
        "removed_list": _node_brief(d["removed"], cur),
    }


def _handle(evt: dict) -> None:
    t = evt.get("type")

    if t in ("save", "force"):
        st.session_state["author"] = evt.get("author", "")
        data = dict(st.session_state["data"])
        data["nodes"] = evt.get("nodes", [])
        data["domains"] = evt.get("domains", {})
        data["rev"] = int(evt.get("rev", data.get("rev", 0)))
        res = store.save_tree(data, evt.get("author", ""), force=(t == "force"))
        if res.ok:
            _set_data(store.load_tree()[0])                   # 정규화된 정본 재로드
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state.pop("conflict", None)
            st.session_state["flash"] = f"저장했습니다 (rev {res.rev})."
        elif res.conflict:
            st.session_state["conflict"] = {"disk_author": res.disk_author, "disk_rev": res.disk_rev}
        else:
            st.session_state["flash"] = res.error or "저장에 실패했습니다."

    elif t == "download":
        # 저장 전 편집분까지 반영해 내보낸다 (컴포넌트가 현재 트리를 함께 보냄)
        data = dict(st.session_state["data"])
        if evt.get("nodes") is not None:
            data["nodes"] = evt["nodes"]
            data["domains"] = evt.get("domains", data.get("domains", {}))
        try:
            if evt.get("fmt") == "json":
                b, name, mime = excel_io.build_json_bytes(data), excel_io.default_filename(ext="json"), "application/json"
            else:
                b, name, mime = excel_io.build_xlsx(data, mask=bool(evt.get("mask", True))), excel_io.default_filename(), _XLSX_MIME
            st.session_state["pending_download"] = (name, base64.b64encode(b).decode(), mime)
        except Exception as e:
            st.session_state["flash"] = f"내보내기 실패: {e}"

    elif t == "import":                     # 파싱 + 미리보기만 (반영 아님)
        try:
            _preview_import(base64.b64decode(evt.get("b64", "")), evt.get("filename", ""))
        except Exception as e:
            st.session_state["import_errors"] = [f"파일을 읽을 수 없습니다: {e}"]
            st.session_state.pop("pending_import", None)

    elif t == "import_apply":               # 사용자가 확인한 뒤에만 반영
        _apply_import(bool(evt.get("delete_missing")), bool(evt.get("add_domains", True)))

    elif t == "import_cancel":
        for k in ("pending_import", "import_preview", "import_errors"):
            st.session_state.pop(k, None)

    elif t == "histpick":                    # 복원 미리보기 (실제 diff 는 파이썬이 계산)
        _preview_restore(evt.get("file") or "", evt.get("nodes"))

    elif t == "restore":
        res, restored = store.restore(evt.get("file") or "", evt.get("author", ""))
        if res.ok and restored is not None:
            _set_data(restored)
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state.pop("diff_preview", None)
            st.session_state["flash"] = f"복원했습니다 (rev {res.rev})."
        else:
            st.session_state["flash"] = res.error or "복원에 실패했습니다."

    elif t == "reload":
        _set_data(store.load_tree()[0])
        st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
        st.session_state.pop("conflict", None)
        st.session_state["flash"] = "최신 내용을 불러왔습니다."


def main() -> None:
    _load()
    flash = st.session_state.pop("flash", "")
    conflict = st.session_state.get("conflict")
    dirty_all = st.session_state.pop("dirty_all", False)

    evt = _component()(**_args(flash, conflict, dirty_all), key="pd_v2", default=None)

    # 대기 중인 다운로드가 있으면 브라우저 다운로드를 트리거(데이터 URI 자동 클릭)
    pend = st.session_state.pop("pending_download", None)
    if pend:
        name, b64, mime = pend
        components.html(
            "<script>const a=document.createElement('a');"
            f"a.href='data:{mime};base64,{b64}';a.download={json.dumps(name)};"
            "document.body.appendChild(a);a.click();a.remove();</script>", height=0)

    # 같은 이벤트 중복 처리 차단 (evt_id = UUID; iframe 리로드에 면역)
    if isinstance(evt, dict) and evt.get("evt_id") and evt.get("evt_id") != st.session_state.get("last_evt"):
        st.session_state["last_evt"] = evt["evt_id"]
        _handle(evt)
        st.rerun()


main()
