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


def _load() -> dict:
    if "data" not in st.session_state:
        data, warns = store.load_tree()
        st.session_state["data"] = data
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
        "author": st.session_state.get("author", ""),
        "env": pc.get_env_label(),
        "history": store.list_history(),
        "audit": store.read_audit(100),
        "flash": flash,
        "conflict": conflict,
        "disk_newer": disk_newer,
        "dirty_all": dirty_all,
    }


def _apply_import(raw_bytes: bytes) -> None:
    data = st.session_state["data"]
    parsed, errs = excel_io.parse_excel(raw_bytes, data)
    if errs:
        st.session_state["flash"] = "엑셀 오류: " + " / ".join(errs[:3])
        return
    d = schema.diff(data, parsed)
    if d["removed"]:                       # 삭제 기본 OFF — 사라진 노드는 되살려 병합
        parsed = schema.normalize({**parsed, "nodes": list(parsed["nodes"]) + [dict(n) for n in d["removed"]]})
        d = schema.diff(data, parsed)
    parsed["rev"] = data.get("rev", 0)
    st.session_state["data"] = parsed
    st.session_state["dirty_all"] = True   # 반영분은 저장 전까지 '미저장'
    st.session_state["flash"] = (f"엑셀 반영: 추가 {len(d['added'])} · 변경 {len(d['changed'])} · "
                                 f"삭제 {len(d['removed'])}. 상단 [저장]을 눌러야 파일에 기록됩니다.")


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
            st.session_state["data"], _ = store.load_tree()   # 정규화된 정본 재로드
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

    elif t == "import":
        try:
            _apply_import(base64.b64decode(evt.get("b64", "")))
        except Exception as e:
            st.session_state["flash"] = f"엑셀을 읽을 수 없습니다: {e}"

    elif t == "restore":
        res, restored = store.restore(evt.get("file") or "", evt.get("author", ""))
        if res.ok and restored is not None:
            st.session_state["data"] = restored
            st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
            st.session_state["flash"] = f"복원했습니다 (rev {res.rev})."
        else:
            st.session_state["flash"] = res.error or "복원에 실패했습니다."

    elif t == "reload":
        st.session_state["data"], _ = store.load_tree()
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
