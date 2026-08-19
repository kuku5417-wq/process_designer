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
        # 이름 붙인 보관본 — history 와 달리 자동 정리되지 않는다(store.get_saves_dir 주석 참조)
        "saves": store.list_saves(),
        "audit": store.read_audit(100),
        "flash": flash,
        "conflict": conflict,
        "disk_newer": disk_newer,
        "dirty_all": dirty_all,
        # 판정은 파이썬이 한다 — 프론트는 결과만 그린다
        "diff_preview": st.session_state.get("diff_preview"),
        "import_preview": st.session_state.get("import_preview"),
        "import_errors": st.session_state.get("import_errors") or [],
        "collect_preview": st.session_state.get("collect_preview"),
        "collect_errors": st.session_state.get("collect_errors") or [],
        # 취합 스캔 폴더 기본값 — 프론트가 빈 칸일 때만 채운다([스캔]만 누르면 되게)
        "collect_folder_default": pc.get_collect_default(),
        # 질의 챗봇 — 키가 없으면 탭 자체를 숨긴다(사외망에서 빈 탭을 보여줄 이유가 없다)
        "chat_ready": _chat_ready(),
        "chat_provider": _chat_provider(),
        "chat_log": st.session_state.get("chat_log") or [],
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


def _collect_files_from_folder(folder: str) -> tuple[list[tuple[str, bytes]], list[str]]:
    """폴더(및 **하위 폴더 전체**)의 *.json 을 읽어 [(파일명, bytes)] 로. 접근 불가·빈 폴더는 오류로.

    파일명은 입력 폴더 기준 **상대경로**(`과A/홍길동.json`) — 하위 폴더가 어디인지 드러나고
    다른 폴더의 동명 파일이 표시상 구분된다(집계 신원은 봉투 우선, 파일명은 basename 폴백).
    """
    errs: list[str] = []
    p = Path(folder)
    if not folder.strip():
        return [], ["폴더 경로를 입력하세요."]
    if not p.exists() or not p.is_dir():
        return [], [f"폴더를 찾을 수 없습니다: {folder}"]
    files: list[tuple[str, bytes]] = []
    try:
        for fp in sorted(p.rglob("*.json"), key=lambda x: x.relative_to(p).as_posix()):
            if not fp.is_file():
                continue
            rel = fp.relative_to(p).as_posix()          # 하위 폴더 포함 상대경로
            try:
                files.append((rel, fp.read_bytes()))
            except Exception as e:
                errs.append(f"{rel}: 읽기 실패 ({e})")
    except Exception as e:
        return [], [f"폴더를 읽을 수 없습니다: {e}"]
    if not files:
        errs.append(f"폴더(하위 폴더 포함)에 .json 파일이 없습니다: {folder}")
    return files, errs


def _preview_collect(files: list[tuple[str, bytes]], base_errs: list[str]) -> None:
    """제출 파일들을 취합해 **미리보기만** 한다 (반영은 collect_apply). import 미리보기와 동형."""
    data = st.session_state["data"]
    if not files:
        st.session_state["collect_errors"] = base_errs or ["취합할 파일이 없습니다."]
        st.session_state.pop("pending_collect", None)
        st.session_state.pop("collect_preview", None)
        return
    merged, reports, errs = excel_io.collect_jsons(files, data)
    if errs:
        st.session_state["collect_errors"] = (base_errs or []) + errs[:20]
        st.session_state.pop("pending_collect", None)
        st.session_state.pop("collect_preview", None)
        return
    st.session_state.pop("collect_errors", None)
    d = schema.diff(data, merged)
    # "제출 N명" 상위 업무 — 인원 많은 순 (nmap 은 merged 기준 경로)
    nmap = schema.node_map(merged.get("nodes", []))
    subs = [n for n in merged.get("nodes", []) if str(n.get("submit_count") or "").isdigit()
            and int(n["submit_count"]) >= 2]
    subs.sort(key=lambda n: int(n["submit_count"]), reverse=True)
    top = [{"path": " › ".join(schema.path_names(nmap, n["id"])[3:]) or n.get("name", ""),
            "count": int(n["submit_count"])} for n in subs[:30]]
    # 제출자 총원 — (부서, 이름) distinct. 트리에는 이름을 저장하지 않으므로 이 수치는
    # 스캔 시점에만 낼 수 있다. 파일 1개 = 제출자 1명이므로 성공 리포트에서 센다.
    ok = [r for r in reports if not r.get("errors")]
    subs_set = {(r.get("dept", ""), r.get("author", "")) for r in ok}
    subs_by_dept: dict[str, int] = {}
    for dept, _author in subs_set:
        k = dept or "(소속 미지정)"
        subs_by_dept[k] = subs_by_dept.get(k, 0) + 1
    # 소속 재설정 건수 — 취합은 이번 스캔의 제출자 소속으로 depts 를 **교체**한다(누적 아님).
    # 제출본의 소속을 고쳐 다시 취합하면 여기 잡힌다. 조용히 바꾸지 않고 미리보기에 보여준다.
    old_depts = {n["id"]: list(n.get("depts") or []) for n in data.get("nodes", [])}
    dept_reset = sum(1 for n in merged.get("nodes", [])
                     if n["id"] in old_depts and old_depts[n["id"]] != list(n.get("depts") or []))
    # 과별 제출값도 같은 규칙으로 **교체**된다 — 바뀐 건수를 함께 띄운다(조용히 바꾸지 않는다).
    old_subs = {n["id"]: n.get("submissions") or [] for n in data.get("nodes", [])}
    submission_reset = sum(1 for n in merged.get("nodes", [])
                           if n["id"] in old_subs and old_subs[n["id"]] != (n.get("submissions") or []))
    # 폴더 부서 ≠ 과의 소속 부서 — 파일을 잘못 놓았을 수 있다. 제외는 하지 않고 알리기만 한다.
    folder_mismatch = [{"filename": r.get("filename", ""), "warn": r.get("warn", "")}
                       for r in reports if r.get("warn")]
    # 빈 가지(lv6 미도달) 삭제 후보 — **미리보기 계산만** 한다.
    # pending_collect 에는 병합만 된 트리를 두고, 실제 제거는 _apply_collect 에서 옵트인일 때만.
    _, empties = excel_io.prune_empty_branches(merged)
    st.session_state["pending_collect"] = merged
    st.session_state["collect_preview"] = {
        "files": reports,
        "scanned": len(files),   # 발견(읽은) JSON 파일 수 — 하위 폴더 포함
        "submitters_total": len(subs_set),
        "submitters_by_dept": dict(sorted(subs_by_dept.items(), key=lambda kv: -kv[1])),
        "removed": len(empties),
        "removed_list": _node_brief(empties, merged),
        "dept_reset": dept_reset,
        "submission_reset": submission_reset,
        "folder_mismatch": folder_mismatch,
        "added": len(d["added"]), "changed": len(d["changed"]),
        "added_list": _node_brief(d["added"], merged),
        "top_submits": top,
        "unknown": excel_io.unknown_domain_values(merged),
        "labels": schema.DOMAIN_LABELS,
        "warns": base_errs,      # 일부 파일 읽기 실패 등 비치명적 경고
    }


def _apply_collect(prune_empty: bool = False) -> None:
    """취합 보류분을 반영. 병합은 추가·병합만 하고, **빈 가지 삭제는 옵트인**이다.

    엑셀 가져오기(_apply_import)는 파싱 결과가 이미 '삭제된 상태'라 옵션이 꺼지면 되살리지만,
    취합은 pending 이 **미삭제 상태**라 반대로 **켜졌을 때만 제거**한다.
    """
    merged = st.session_state.get("pending_collect")
    if not merged:
        st.session_state["flash"] = "반영할 취합 결과가 없습니다. 파일을 다시 스캔하세요."
        return
    data = st.session_state["data"]
    doms = merged.setdefault("domains", {})
    for k, vals in excel_io.unknown_domain_values(merged).items():
        for v in vals:
            if v not in doms.setdefault(k, []):
                doms[k].append(v)
    pruned = 0
    if prune_empty:
        merged, empties = excel_io.prune_empty_branches(merged)
        pruned = len(empties)
    d = schema.diff(data, merged)          # 삭제까지 반영된 최종 트리로 diff
    merged["rev"] = data.get("rev", 0)
    _set_data(merged)
    st.session_state["dirty_all"] = True
    for k in ("pending_collect", "collect_preview", "collect_errors"):
        st.session_state.pop(k, None)
    st.session_state["flash"] = (
        f"취합 반영: 추가 {len(d['added'])} · 변경 {len(d['changed'])}"
        + (f" · 빈 가지 삭제 {pruned}" if pruned else "")
        + ". 상단 [저장]을 눌러야 파일에 기록됩니다.")


def _handle_chat(evt: dict) -> None:
    """질의 챗봇 — 트리를 컨텍스트로 LLM 에 한 번 묻고 답을 대화록에 쌓는다.

    ★ `_set_data()` 를 부르지 않는다 — 조회성 왕복이라 tree_epoch 이 바뀌면 안 된다.
      epoch 이 오르면 프론트가 자기 트리를 저장본으로 갈아끼워 **미저장 편집이 사라진다.**
    ★ 화면의 미저장 편집까지 포함해 답해야 하므로 컴포넌트가 보낸 nodes 를 우선 쓴다.
    """
    q = str(evt.get("q") or "").strip()
    if not q:
        return
    log: list[dict] = st.session_state.setdefault("chat_log", [])
    log.append({"role": "user", "text": q})
    data = dict(st.session_state["data"])
    if evt.get("nodes") is not None:                      # 저장 전 편집분 반영
        data["nodes"] = evt["nodes"]
        data["domains"] = evt.get("domains", data.get("domains", {}))
    try:
        # 지연 import — 모듈·키가 없어도 앱 전체가 죽지 않게 (공통규칙 4 폴백)
        import chat_context
        import llm_client
        prompt = chat_context.build_prompt(data, q, log[:-1])
        ans = llm_client.call_llm_text(prompt, system=chat_context.SYSTEM_PROMPT)
        if ans:
            log.append({"role": "bot", "text": ans})
        else:
            errs = "; ".join(llm_client.last_errors()) or "알 수 없는 오류"
            log.append({"role": "bot", "text": f"답변을 받지 못했습니다. ({errs})", "err": True})
    except Exception as e:                                # noqa: BLE001 — LLM 실패로 앱이 죽지 않게
        log.append({"role": "bot", "text": f"질의 처리 중 오류: {type(e).__name__}: {e}", "err": True})
    st.session_state["chat_log"] = log[-40:]              # 대화록 상한 (세션 메모리만, 저장 안 함)


def _chat_ready() -> bool:
    """LLM 키가 설정돼 있는가. 값은 절대 보지 않는다(설정 여부만)."""
    try:
        import llm_client
        return llm_client.is_configured()
    except Exception:   # noqa: BLE001 — requests 미설치 등 → 챗봇 탭만 숨기고 앱은 뜬다
        return False


def _chat_provider() -> str:
    """설정된 LLM provider 표시 문자열 (키 값은 노출하지 않는다 — 이름만)."""
    try:
        import llm_client
        return f"({llm_client.provider_label()} 순으로 시도)"
    except Exception:   # noqa: BLE001 — 표시용이라 실패해도 무시
        return ""


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

    elif t == "collect_scan":               # 다수 제출 취합 — 파싱·미리보기만
        try:
            folder = evt.get("folder")
            if folder is not None:
                files, base_errs = _collect_files_from_folder(str(folder))
            else:
                files, base_errs = [], []
                for f in evt.get("files") or []:
                    try:
                        files.append((f.get("name", ""), base64.b64decode(f.get("b64", ""))))
                    except Exception as e:
                        base_errs.append(f"{f.get('name', '')}: 디코드 실패 ({e})")
            _preview_collect(files, base_errs)
        except Exception as e:
            st.session_state["collect_errors"] = [f"취합 중 오류: {e}"]
            st.session_state.pop("pending_collect", None)

    elif t == "collect_apply":
        _apply_collect(bool(evt.get("prune_empty")))

    elif t == "collect_cancel":
        for k in ("pending_collect", "collect_preview", "collect_errors"):
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

    elif t == "save_as":                     # 현재 화면 트리를 이름 붙여 보관 (정본은 그대로)
        data = dict(st.session_state["data"])
        if evt.get("nodes") is not None:     # 저장 전 편집분까지 담는다 (download 와 같은 규칙)
            data["nodes"] = evt["nodes"]
            data["domains"] = evt.get("domains", data.get("domains", {}))
        # 같은 이름이면 덮지 않고 다음 버전으로 쌓인다 — 확인 모달이 필요 없는 이유(잃는 게 없다).
        res = store.save_named(data, evt.get("name", ""), evt.get("author", ""))
        st.session_state["flash"] = (f"'{evt.get('name', '')}' v{res.version} 으로 보관했습니다."
                                     if res.ok else (res.error or "보관에 실패했습니다."))

    elif t == "saves_load":
        # ★ 정본을 바로 덮지 않는다 — 화면(세션)에만 올리고 사용자가 [저장]을 눌러야 파일에 쓴다.
        #   취합 반영(_apply_collect)과 같은 규칙이다. 스냅샷 복원(restore)이 즉시 저장하는 것과
        #   일부러 다르게 뒀다: 보관본은 "되돌리기"가 아니라 "이 버전을 가져와 이어서 작업"이라
        #   실수로 눌렀을 때 정본이 이미 덮여 있으면 안 된다.
        _v = evt.get("version")
        loaded = store.load_named(evt.get("name") or "", None if _v in (None, "") else int(_v))
        if loaded is None:
            st.session_state["flash"] = "보관본을 읽을 수 없습니다."
        else:
            cur = st.session_state["data"]
            loaded["rev"] = cur.get("rev", 0)         # 정본 rev 유지 — 저장 시 충돌검사가 정상 동작
            _set_data(loaded)
            st.session_state["dirty_all"] = True
            st.session_state["flash"] = (
                f"'{evt.get('name', '')}' v{loaded.get('saved_version', '?')} 를 화면에 불러왔습니다 "
                f"(업무 {len(loaded.get('nodes', []))}개). 상단 [저장]을 눌러야 파일에 기록됩니다.")

    elif t == "saves_delete":
        _dv = evt.get("version")
        ok = store.delete_named(evt.get("name") or "", None if _dv in (None, "") else int(_dv))
        st.session_state["flash"] = ("보관본을 삭제했습니다." if ok else "보관본을 삭제하지 못했습니다.")

    elif t == "reload":
        _set_data(store.load_tree()[0])
        st.session_state["disk_seen_mtime"] = store.disk_stat()[0]
        st.session_state.pop("conflict", None)
        st.session_state["flash"] = "최신 내용을 불러왔습니다."

    elif t == "chat":                        # 질의 챗봇 — 트리 질의응답(읽기 전용)
        _handle_chat(evt)

    elif t == "chat_clear":
        st.session_state["chat_log"] = []


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
