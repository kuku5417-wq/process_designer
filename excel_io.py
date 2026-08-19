"""excel_io.py — 프로세스 계층도 ↔ 엑셀 변환.

다운로드: 노드 1개 = 행 1개. 조상 이름을 lv0~lv7 컬럼에 채우고 자기 이름은 자기 레벨
컬럼에 둔다(오른쪽은 공란) — 엑셀에서 그대로 눈에 보이는 아웃라인이 되고 피벗도 된다.
lv0~lv2 는 저장 데이터에 없는 고정값이므로 이 시점에 재부착한다.

업로드: 같은 포맷을 읽어 id 로 기존 노드와 매칭하고, 부모는 lv 경로로 해석한다.

담당자(owner) 컬럼은 **폐지됐다** — 제출자가 자기 일을 정의한 것이라 이름은 제출자와
중복이고 어떤 계산도 좌우하지 않았다(공통규칙 7 최소수집). 옛 엑셀에 그 열이 남아 있어도
무시된다(오류 아님).
"""
from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd

import schema
from pii import mask_name

SHEET_TREE = "계층도"
SHEET_DOMAIN = "도메인"
SHEET_SUMMARY = "요약"
SHEET_SUBMIT = "제출상세"   # 과별 제출값 원본 (long format, 표시 전용 — parse_excel 은 읽지 않는다)

LV_COLS = [f"lv{i}" for i in range(0, schema.LEVEL_MAX + 1)]      # lv0..lv7

# 엑셀 컬럼명 → 노드 필드명
FIELD_COLS: dict[str, str] = {
    "AI에이전트": "has_ai_agent",
    "활용기술": "tech",
    "부서/과": "dept",
    "자동화수준": "automation_level",
    "수행주기": "frequency",
    "1회소요시간(h)": "work_hours",
    "기간단위": "freq_unit",
    "횟수": "freq_count",
    "연간횟수": "annual_count",
    "산출물": "outputs",
    "향후AI적용기술": "future_tech",   # 다중 — tech 와 같은 도메인, ", " 조인
    "특이사항": "special_note",        # 다중 — SG/DF(LNG)/메탄올/LPG
    "적용선종": "ship_types",          # 다중 — CNT/COT/LNG/…
    "연계시스템": "linked_system",     # (구) 단일 — back-compat
    "연계시스템 추가정보": "linked_system_detail",
    "업무설명": "desc",
    "제출인원": "submit_count",      # 취합 산출물 — 이 업무를 제출한 인원수 N
    "취합상세": "submit_detail",     # 취합 산출물 — 제출자별 상세(부서 기준, 이름 없음)
}

# 파생 컬럼은 읽지 않고 쓰기만 한다 (역수입 금지). 연간공수=work_hours×annual_count, 상위부서=dept_parent(과)
# 연계시스템(전체)=linked_systems 다건 조인(객체배열이라 엑셀 한 칸에 못 담아 표시용 문자열; 정본은 JSON)
# 제출과수·제출합계공수(h) 는 submissions[] 파생이다. **DERIVED_COLS 에만** 둘 것 —
# FIELD_COLS 에 넣으면 parse_excel 이 파생값을 저장 필드로 역수입한다(위 규칙).
DERIVED_COLS: list[str] = ["연간공수(h)", "상위부서", "연계시스템(전체)", "제출과수", "제출합계공수(h)"]

# '저장자' = 이 노드를 마지막으로 저장한 사람(updated_by). 작성자가 아니다 — 메인앱은 취합본을
# 다듬는 관리 도구라 상단에서도 '저장자'만 받는다. parse_excel 은 이 열을 읽지 않는다(base 보존).
TREE_COLS: list[str] = (LV_COLS + ["레벨", "이름"] + list(FIELD_COLS) + DERIVED_COLS
                        + ["저장자", "수정일시", "id"])


def _dfs_order(data: dict) -> list[dict]:
    """DFS pre-order — 엑셀에서 부모가 항상 자식보다 위에 온다 (업로드 시 경로 해석의 전제)."""
    idx = schema.children_index(data.get("nodes", []))
    out: list[dict] = []
    stack = list(reversed(idx.get(schema.ROOT_ID, [])))
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(idx.get(n["id"], [])))
    return out


def flatten(data: dict, mask: bool = True) -> pd.DataFrame:
    """계층도를 엑셀용 평면 표로. mask=True 면 작성자 이름을 마스킹한다."""
    nmap = schema.node_map(data.get("nodes", []))
    rows: list[dict] = []
    for n in _dfs_order(data):
        names = schema.path_names(nmap, n["id"])          # lv0..자기자신
        row: dict[str, object] = {c: "" for c in TREE_COLS}
        for i, nm in enumerate(names[: schema.LEVEL_MAX + 1]):
            row[f"lv{i}"] = nm
        row["레벨"] = n.get("level", "")
        row["이름"] = n.get("name", "")
        row["AI에이전트"] = "Y" if n.get("has_ai_agent") else "N"
        row["활용기술"] = ", ".join(n.get("tech") or [])
        row["부서/과"] = n.get("dept", "")
        row["상위부서"] = schema.dept_parent(n.get("dept")) if n.get("dept") else ""   # 파생(과→부서)
        row["자동화수준"] = n.get("automation_level", "")
        row["수행주기"] = n.get("frequency", "")
        row["1회소요시간(h)"] = n.get("work_hours", "")
        row["기간단위"] = n.get("freq_unit", "")
        row["횟수"] = n.get("freq_count", "")
        ac = schema.annual_count_of(n)          # freq_unit×freq_count 파생, 없으면 annual_count 폴백
        row["연간횟수"] = int(ac) if ac and ac == int(ac) else (ac or "")
        ah = schema.annual_hours(n)
        row["연간공수(h)"] = ah if ah else ""
        row["산출물"] = n.get("outputs", "")
        row["향후AI적용기술"] = ", ".join(n.get("future_tech") or [])
        row["특이사항"] = ", ".join(n.get("special_note") or [])
        row["적용선종"] = ", ".join(n.get("ship_types") or [])
        row["연계시스템"] = n.get("linked_system", "")
        row["연계시스템 추가정보"] = n.get("linked_system_detail", "")
        # 연계시스템 다건 → "SAP · MM모듈 / NONSAP · …" (표시용, 역수입 안 함)
        row["연계시스템(전체)"] = " / ".join(
            (str(e.get("system") or "") + (" · " + str(e.get("detail")) if e.get("detail") else ""))
            for e in (n.get("linked_systems") or []) if isinstance(e, dict) and (e.get("system") or e.get("detail")))
        row["업무설명"] = n.get("desc", "")
        row["제출인원"] = n.get("submit_count", "")            # 취합 산출물 (없으면 빈값)
        row["취합상세"] = n.get("submit_detail", "")           # 부서 기준, 이름 없음
        subs = schema.submissions_of(n)
        row["제출과수"] = len({r.get("dept") for r in subs}) if subs else ""
        # 제출 합계 부하 — 과별 제출값을 각각 연간화해 더한 값. **대표값 연간공수와 다른 게 정상**이다
        # (대표값은 첫 제출자 1명분, 이쪽은 수행 과 전부의 합). 저장하지 않고 여기서만 계산한다.
        sh = sum(schema.annual_hours(r) for r in subs)
        row["제출합계공수(h)"] = round(sh, 2) if sh else ""
        row["저장자"] = mask_name(n.get("updated_by", "")) if mask else n.get("updated_by", "")
        row["수정일시"] = n.get("updated_at", "")
        row["id"] = n["id"]
        rows.append(row)
    return pd.DataFrame(rows, columns=TREE_COLS)


def _domain_df(data: dict) -> pd.DataFrame:
    doms = data.get("domains", {})
    cols = {schema.DOMAIN_LABELS.get(k, k): list(v) for k, v in doms.items()}
    if not cols:
        return pd.DataFrame()
    width = max(len(v) for v in cols.values())
    return pd.DataFrame({k: v + [""] * (width - len(v)) for k, v in cols.items()})


def _summary_df(data: dict, mask: bool = True) -> pd.DataFrame:
    """요약 시트. AI·부서·자동화 지표의 분모는 lv6 세부업무 (상위 레벨은 상세를 입력하지 않는다).

    AI·활용기술은 **현재/향후 두 축**으로 나눠 낸다 — 한 칸에 섞으면 "지금 되는 것"과
    "하고 싶은 것"이 구분되지 않는다. 기술 축은 다중선택이라 합계가 lv6 수보다 크다(정상).
    """
    s = schema.stats(data)
    lv6 = f"lv{schema.FULL_DETAIL_LEVEL} {schema.LEVEL_LABELS[schema.FULL_DETAIL_LEVEL]}"
    multi = "※ 다중선택 — 한 업무가 여러 값을 가지면 각 값에 1회씩(합계 ≠ 업무 수)"
    # 활용기술 집계는 **사용량 축**이라 AI 여부와 무관하다 — 제외 기술도 여기엔 그대로 잡힌다.
    # 이 문구가 없으면 "SAP 7건인데 AI는 0건" 이 버그로 읽힌다.
    tech_note = "※ 사용량 기준 — AI 카운트 제외 기술도 포함됩니다(AI 적용률과 축이 다름)"
    # 과·부서 축은 **수행 주체 기준**이라 한 업무가 여러 과에 잡힌다. lv6 수와 단위가 다르다.
    multi_dept = "※ 한 업무를 여러 과가 수행하면 각 과에 1회씩 — 합계는 세부업무 수와 다릅니다"
    rows: list[dict] = [
        {"구분": "전체", "항목": "업무 수(전 레벨)", "값": s["total"]},
        {"구분": "전체", "항목": f"{lv6} 수", "값": s["detail_total"]},
        {"구분": "전체", "항목": "부하 미입력", "값": s["missing_total"]},
        {"구분": "전체", "항목": "호선루틴 보류", "값": s["unresolved_total"]},
        {"구분": f"AI 에이전트 — 현재 ({lv6} 기준)", "항목": "적용", "값": s["ai_yes"]},
        {"구분": f"AI 에이전트 — 현재 ({lv6} 기준)", "항목": "미적용", "값": s["ai_no"]},
        {"구분": f"AI 에이전트 — 현재 ({lv6} 기준)", "항목": "적용률(%)", "값": s["ai_rate"]},
        {"구분": f"AI 에이전트 — 향후 ({lv6} 기준)", "항목": "적용", "값": s["ai_future_yes"]},
        {"구분": f"AI 에이전트 — 향후 ({lv6} 기준)", "항목": "미적용", "값": s["ai_future_no"]},
        {"구분": f"AI 에이전트 — 향후 ({lv6} 기준)", "항목": "적용률(%)", "값": s["ai_future_rate"]},
    ]
    for lv, c in s["by_level"].items():
        rows.append({"구분": "레벨별", "항목": f"lv{lv} ({schema.LEVEL_LABELS.get(lv, '')})", "값": c})
    for d, c in s["by_dept"].items():
        rows.append({"구분": f"과별 ({lv6} 기준)", "항목": d, "값": c, "비고": multi_dept})
    # 부서 롤업 — 화면(JS)은 부서별로 보여주는데 엑셀엔 없어서 축이 어긋나 있었다
    for g, c in s["by_dept_group"].items():
        rows.append({"구분": f"부서별 ({lv6} 기준)", "항목": g, "값": c, "비고": multi_dept})
    for a, c in s["by_automation"].items():
        rows.append({"구분": f"자동화수준별 ({lv6} 기준)", "항목": a, "값": c})
    for t, c in s["by_tech_now"].items():
        rows.append({"구분": f"활용기술 — 현재 ({lv6} 기준)", "항목": t, "값": c, "비고": multi + " " + tech_note})
    for t, c in s["by_tech_future"].items():
        rows.append({"구분": f"활용기술 — 향후 ({lv6} 기준)", "항목": t, "값": c, "비고": multi + " " + tech_note})
    # AI 적용률이 왜 그 숫자인지의 근거를 엑셀 안에 남긴다 — 화면을 안 보는 사람이 받는 파일이다.
    for t in (data.get("domains", {}).get("tech_no_ai") or []):
        rows.append({"구분": "AI 카운트 제외 기술", "항목": t, "값": "",
                     "비고": "이 기술만 가진 업무는 AI 적용에서 제외됩니다"})
    return pd.DataFrame(rows)


SUBMIT_COLS: list[str] = (["lv3", "lv4", "lv5", "lv6", "lv7", "과", "제출자", "상위부서", "인원"]
                          + ["발생패턴", "기간단위", "횟수", "1회소요시간(h)", "연간공수(h)",
                             "자동화수준", "현재기술", "향후기술", "산출물", "적용선종", "특이사항"])


def _submission_df(data: dict, mask: bool = True) -> pd.DataFrame:
    """제출상세 시트 — **1행 = (업무 × 제출한 과)**.

    계층도 시트에 행을 늘려 담을 수 없다: 그 시트는 parse_excel 의 입력이고, 같은 이름 경로가
    두 행이면 "경로가 중복됩니다" 오류로 **업로드가 통째로 막힌다**. 그래서 별도 시트로 뺐고,
    이 시트는 **역수입하지 않는다**(정본은 트리 JSON 의 submissions).

    대표값(계층도 시트)은 첫 제출자 1명분이라, 과별 비교는 반드시 이 시트를 봐야 한다.
    """
    nmap = schema.node_map(data.get("nodes", []))
    rows: list[dict] = []
    for n in _dfs_order(data):
        subs = schema.submissions_of(n)
        if not subs:
            continue
        names = schema.path_names(nmap, n["id"])          # lv0..자기자신
        for r in subs:
            row: dict[str, object] = {c: "" for c in SUBMIT_COLS}
            for i in range(3, schema.LEVEL_MAX + 1):
                if i < len(names):
                    row[f"lv{i}"] = names[i]
            row["과"] = r.get("dept", "")
            # 제출자는 **출력에서 마스킹**한다(계층도 시트의 저장자 열과 같은 규칙).
            # ★ mask 를 안 받고 원본을 내면 build_xlsx(mask=True) 인데 이 시트만 실명이 나간다.
            _au = r.get("author", "")
            row["제출자"] = (mask_name(_au) if mask else _au)
            row["상위부서"] = schema.dept_parent(r.get("dept"))
            row["인원"] = r.get("count", 1)
            row["발생패턴"] = r.get("occur_pattern", "")
            row["기간단위"] = r.get("freq_unit", "")
            row["횟수"] = r.get("freq_count", "")
            row["1회소요시간(h)"] = r.get("work_hours", "")
            ah = schema.annual_hours(r)                    # 파생 — 저장하지 않는다
            row["연간공수(h)"] = ah if ah else ""
            row["자동화수준"] = r.get("automation_level", "")
            row["현재기술"] = ", ".join(r.get("tech") or [])
            row["향후기술"] = ", ".join(r.get("future_tech") or [])
            row["산출물"] = r.get("outputs", "")
            row["적용선종"] = ", ".join(r.get("ship_types") or [])
            row["특이사항"] = ", ".join(r.get("special_note") or [])
            rows.append(row)
    return pd.DataFrame(rows, columns=SUBMIT_COLS) if rows else pd.DataFrame()


def build_xlsx(data: dict, mask: bool = True) -> bytes:
    """엑셀 bytes — 계층도 / 도메인 / 요약 (+ 취합본이면 제출상세)."""
    buf = io.BytesIO()
    tree = flatten(data, mask=mask)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tree.to_excel(xw, sheet_name=SHEET_TREE, index=False, freeze_panes=(1, 0))
        dom = _domain_df(data)
        if not dom.empty:
            dom.to_excel(xw, sheet_name=SHEET_DOMAIN, index=False, freeze_panes=(1, 0))
        _summary_df(data, mask=mask).to_excel(xw, sheet_name=SHEET_SUMMARY, index=False, freeze_panes=(1, 0))
        # 과별 제출값이 하나도 없으면(취합 전 트리) 시트를 만들지 않는다 — _domain_df 와 같은 규칙.
        sub = _submission_df(data, mask=mask)
        if not sub.empty:
            sub.to_excel(xw, sheet_name=SHEET_SUBMIT, index=False, freeze_panes=(1, 0))

        widths = {"lv0": 8, "lv1": 8, "lv2": 10, "lv3": 16, "lv4": 20, "lv5": 20, "lv6": 22, "lv7": 22,
                  "레벨": 6, "이름": 22, "AI에이전트": 11, "활용기술": 20, "부서/과": 12,
                  "자동화수준": 11, "수행주기": 10,
                  "산출물/연계시스템": 26, "업무설명": 40, "저장자": 10, "수정일시": 20, "id": 12}
        ws = xw.sheets[SHEET_TREE]
        for i, c in enumerate(tree.columns, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(c, 14)
    return buf.getvalue()


def build_json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# ── 업로드 ──────────────────────────────────────────────

def _cell(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _current_path_ids(current: dict) -> dict[tuple, str]:
    """현재 트리의 (lv3~자기자신 이름 경로) → id. 경로 매칭 폴백의 색인."""
    nmap = schema.node_map(current.get("nodes", []))
    out: dict[tuple, str] = {}
    for n in current.get("nodes", []):
        out[tuple(schema.path_names(nmap, n["id"])[3:])] = n["id"]
    return out


def _match(nid: str, path: tuple, old: dict, cur_paths: dict, used: set) -> tuple[str, dict | None]:
    """들어온 행/노드를 기존 노드와 짝지어 (id, 원본) 반환. 없으면 (새 id, None).

    1) id 가 현재 트리에 있으면 **id 우선** — 이름을 바꾼 노드도 정확히 따라간다.
    2) **lv3(부문)만** 이름으로 매칭 — 개인 배포판에서 각자 만든 같은 이름의 부문이
       인원수만큼 쌓이는 것을 막는다.
       ★ lv4~lv6 에는 절대 걸지 말 것. 두 사람이 우연히 같은 경로를 만들면 한 노드로
         합쳐지며 뒷사람 값이 앞사람을 조용히 덮어쓴다. 부문만 합치면 충분하고,
         하위가 겹치면 눈에 보이는 중복으로 남겨 관리자가 판단하는 편이 안전하다.
    3) 들어온 id 는 모르는 값이어도 **그대로 채택**한다 — 같은 사람이 다시 제출했을 때
       자기 업무가 갱신되지 신규로 복제되지 않는다(멱등).
       단 이미 다른 노드가 쓰는 id 면 새로 발급한다(충돌 가드).
    """
    if nid and nid in old:
        return nid, old[nid]
    if len(path) == 1:                       # lv3 = 경로 길이 1
        hit = cur_paths.get(path)
        if hit and hit in old:
            return hit, old[hit]
    if nid and nid not in used:
        return nid, None
    return schema.new_id(), None


def parse_excel(xlsx: bytes, current: dict) -> tuple[dict, list[str]]:
    """엑셀 bytes → 새 트리. (data, 오류목록). 오류가 있으면 data 는 신뢰하지 말 것.

    · id → 이름 경로 순으로 기존 노드와 매칭(created_at 보존), 둘 다 없으면 신규
    · 부모는 lv3~lv6 경로로 해석 — 부모 행이 반드시 존재해야 한다
    """
    errs: list[str] = []
    try:
        df = pd.read_excel(io.BytesIO(xlsx), sheet_name=SHEET_TREE, dtype=object)
    except Exception as e:
        return current, [f"'{SHEET_TREE}' 시트를 읽을 수 없습니다: {e}"]

    missing = [c for c in LV_COLS[schema.LEVEL_MIN:] if c not in df.columns]
    if missing:
        return current, [f"필수 컬럼이 없습니다: {', '.join(missing)}"]

    old = schema.node_map(current.get("nodes", []))
    cur_paths = _current_path_ids(current)
    rows: list[dict] = []
    for i, r in df.iterrows():
        excel_row = int(i) + 2       # 헤더 1줄 + 0-base
        path = [_cell(r.get(f"lv{lv}")) for lv in range(schema.LEVEL_MIN, schema.LEVEL_MAX + 1)]
        # 비어 있지 않은 칸이 연속(prefix)인지 확인 — lv4 공란인데 lv5 만 있으면 오류
        filled = [j for j, v in enumerate(path) if v]
        if not filled:
            continue                 # 전부 공란인 행은 조용히 무시 (엑셀 꼬리 빈 줄)
        if filled != list(range(len(filled))):
            errs.append(f"{excel_row}행: lv 컬럼이 중간에 비어 있습니다. 왼쪽부터 연속으로 채워 주세요.")
            continue
        path = path[: len(filled)]
        rows.append({"row": excel_row, "path": tuple(path), "r": r})

    dup_paths = [p for p in (x["path"] for x in rows) if [y["path"] for y in rows].count(p) > 1]
    if dup_paths:
        for p in sorted(set(dup_paths)):
            errs.append(f"경로가 중복됩니다: {' › '.join(p)}")

    # 얕은 것부터 처리 → 부모가 먼저 등록된다
    rows.sort(key=lambda x: len(x["path"]))
    path_id: dict[tuple, str] = {}
    nodes: list[dict] = []
    order_counter: dict[str, int] = {}
    used_ids: set[str] = set()

    for item in rows:
        r, path, excel_row = item["r"], item["path"], item["row"]
        parent_path = path[:-1]
        if parent_path:
            parent_id = path_id.get(parent_path)
            if parent_id is None:
                errs.append(f"{excel_row}행: 상위 업무 '{' › '.join(parent_path)}' 행이 없습니다.")
                continue
        else:
            parent_id = schema.ROOT_ID

        nid, base = _match(_cell(r.get("id")), path, old, cur_paths, used_ids)
        used_ids.add(nid)
        level = schema.LEVEL_MIN + len(path) - 1

        tech_raw = _cell(r.get("활용기술"))
        _split = lambda s: [t.strip() for t in _cell(s).split(",") if t.strip()]
        node = {
            "id": nid,
            "parent_id": parent_id,
            "level": level,
            "order": order_counter.get(parent_id, 0),
            "name": path[-1],
            "desc": _cell(r.get("업무설명")),
            "dept": _cell(r.get("부서/과")),
            "has_ai_agent": _cell(r.get("AI에이전트")).upper() in ("Y", "YES", "TRUE", "1", "O"),
            "tech": [t.strip() for t in tech_raw.split(",") if t.strip()],
            "automation_level": _cell(r.get("자동화수준")),
            "frequency": _cell(r.get("수행주기")),
            "work_hours": _cell(r.get("1회소요시간(h)")),
            "freq_unit": _cell(r.get("기간단위")),
            "freq_count": _cell(r.get("횟수")),
            # 연간공수(h)·연간횟수 는 freq_unit×freq_count 파생값이라 authoritative 하게 읽지 않는다
            # (구 데이터 폴백용으로 annual_count 만 보존).
            "annual_count": _cell(r.get("연간횟수")),
            "outputs": _cell(r.get("산출물")),
            "future_tech": _split(r.get("향후AI적용기술")),   # 다중 왕복
            "special_note": _split(r.get("특이사항")),
            "ship_types": _split(r.get("적용선종")),
            "linked_system": _cell(r.get("연계시스템")),
            "linked_system_detail": _cell(r.get("연계시스템 추가정보")),
            # 연계시스템 다건(linked_systems)은 객체배열이라 엑셀에서 역수입하지 않는다(JSON 정본).
            # base(기존 노드)에 있으면 보존한다.
            "linked_systems": (base or {}).get("linked_systems", []),
            "submit_count": _cell(r.get("제출인원")),          # 취합 산출물 왕복 보존
            "submit_detail": _cell(r.get("취합상세")),
            # 과별 제출값(submissions)은 객체배열이라 엑셀 셀 한 칸에 못 담는다(linked_systems 와 같은 이유).
            # ★ base 에서 **반드시** 보존할 것 — 빠뜨리면 관리자가 엑셀을 한 번 내려받아 올리는
            #   순간 부서별 값이 통째로 사라진다. '제출상세' 시트는 표시용이라 역수입하지 않는다.
            "submissions": (base or {}).get("submissions", []),
            "created_at": (base or {}).get("created_at", schema.now_iso()),
            "updated_at": (base or {}).get("updated_at", schema.now_iso()),
            "updated_by": (base or {}).get("updated_by", ""),
        }
        order_counter[parent_id] = node["order"] + 1
        path_id[path] = nid
        nodes.append(node)

    out = {
        "schema_version": schema.SCHEMA_VERSION,
        "rev": current.get("rev", 0),
        "updated_at": current.get("updated_at", schema.now_iso()),
        "updated_by": current.get("updated_by", ""),
        "nodes": nodes,
        "domains": {k: list(v) for k, v in current.get("domains", {}).items()},
    }
    return schema.normalize(out), errs


def parse_json(raw: bytes, current: dict) -> tuple[dict, list[str]]:
    """개인 배포판(standalone)이 내보낸 JSON → 새 트리. (data, 오류목록).

    개인 파일은 **자기가 만든 부분만** 담고 있다. 여기서 나온 트리를 현재 트리와 diff 하면
    파일에 없는 남의 업무가 전부 '삭제 대상'으로 잡히는데, 삭제 옵트인이 기본 OFF 라
    되살려 병합된다 — 그래서 여러 사람 파일을 순차로 올려도 서로의 작업이 보존된다.

    id 는 그대로 살린다 — 같은 사람이 두 번 올리면 자기 노드가 갱신되지 신규로 쌓이지 않는다.
    모르는 id 는 이름 경로로 한 번 더 맞춰본다(_match) — 각자 만든 같은 이름의 부문을 합친다.
    """
    errs: list[str] = []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return current, [f"JSON 을 읽을 수 없습니다: {e}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        return current, ["이 앱에서 내보낸 JSON 이 아닙니다 (nodes 배열이 없습니다)."]

    try:
        incoming = schema.normalize({
            "nodes": [dict(n) for n in payload["nodes"] if isinstance(n, dict)],
            "domains": payload.get("domains") or {},
        })
    except Exception as e:
        return current, [f"JSON 구조가 올바르지 않습니다: {e}"]
    if not incoming["nodes"]:
        return current, ["JSON 에 업무가 없습니다."]

    old = schema.node_map(current.get("nodes", []))
    cur_paths = _current_path_ids(current)
    fmap = schema.node_map(incoming["nodes"])

    # 1) 파일의 각 노드를 기존 노드에 짝지어 최종 id 를 정한다.
    #    얕은 것부터 — lv3 이 먼저 확정돼야 아래에서 부모를 제대로 가리킨다.
    idmap: dict[str, str] = {}
    bases: dict[str, dict] = {}
    used_ids: set[str] = set()
    ordered = sorted(incoming["nodes"], key=lambda n: n.get("level", 3))
    for n in ordered:
        path = tuple(schema.path_names(fmap, n["id"])[3:])
        fid, base = _match(n["id"], path, old, cur_paths, used_ids)
        used_ids.add(fid)
        idmap[n["id"]] = fid
        if base:
            bases[fid] = base

    # 2) id 를 갈아끼운다. parent_id 를 먼저 바꾸면 원본 id 를 잃으므로 새 리스트로 만든다
    nodes: list[dict] = []
    for n in incoming["nodes"]:
        fid = idmap[n["id"]]
        base = bases.get(fid, {})
        m = dict(n)
        m["id"] = fid
        m["parent_id"] = (schema.ROOT_ID if n["parent_id"] == schema.ROOT_ID
                          else idmap.get(n["parent_id"], n["parent_id"]))
        m["created_at"] = base.get("created_at", n.get("created_at") or schema.now_iso())
        # 취합 산출물은 **개인 파일이 들고 있지 않다**(soloExport 가 비운다). 그대로 덮으면
        # 개인 JSON 하나를 이어붙이는 것만으로 그 노드의 과별 제출값·제출인원이 날아간다.
        # 이 셋은 취합만이 쓰는 필드이므로 기존 노드 값을 그대로 살린다.
        for _k in ("submissions", "submit_count", "submit_detail"):
            if base.get(_k):
                m[_k] = base[_k]
        nodes.append(m)

    # 3) 도메인 마스터는 **현재 것을 그대로 쓴다** — 파일이 들고 온 목록은 버린다.
    #    parse_excel 이 엑셀의 도메인 시트를 무시하는 것과 같은 규칙이다. 개인이 임의로
    #    만든 용어가 마스터에 바로 섞이면 20명분이 취합될 때 용어가 갈린다.
    #    개인이 새로 쓴 기술은 노드의 tech 배열에 실려 오고, unknown_domain_values 가
    #    잡아내 관리자가 [도메인 목록에 추가] 로 승인한다 — 이미 있는 관문을 우회하지 말 것.
    out = {
        "schema_version": schema.SCHEMA_VERSION,
        "rev": current.get("rev", 0),                  # 파일의 rev 는 무시 (충돌검사 정본은 서버)
        "updated_at": current.get("updated_at", schema.now_iso()),
        "updated_by": current.get("updated_by", ""),
        "nodes": nodes,
        "domains": {k: list(v) for k, v in current.get("domains", {}).items()},
    }
    return schema.normalize(out), errs


def _dept_from_path(relpath: str) -> tuple[str, str]:
    """스캔 폴더 기준 상대경로에서 (부서, 과) — **첫 하위폴더=부서, 두 번째 하위폴더=과**.

    운영 공유폴더가 `<스캔폴더>/시운전1부/기장운전1과/프로세스_홍길동_20260819.json` 구조라
    폴더 계층이 곧 소속이다. 봉투(exported_dept)는 개인이 상단에서 고른 값이라 폴더와 어긋날 수
    있고 정정하려면 파일을 다시 받아야 하지만, 폴더는 관리자가 파일을 옮기기만 하면 고쳐진다.

    깊이가 모자라면 있는 만큼만 돌려준다 — 루트 직하 파일과 브라우저 다중업로드는 ("", "") 라
    호출 측이 봉투·파일명 폴백으로 넘어간다. 3단 이하 하위폴더는 무시한다.
    """
    parts = [x for x in str(relpath or "").replace("\\", "/").split("/") if x.strip()]
    dirs = parts[:-1]                                  # 마지막 조각은 파일명
    return (dirs[0].strip() if len(dirs) >= 1 else "",
            dirs[1].strip() if len(dirs) >= 2 else "")


def _submitter_of(payload: dict, filename: str) -> tuple[str, str]:
    """제출자 신원 (부서, 이름). 봉투(exported_dept/by) 우선, 없으면 파일명에서 파싱.

    파일명 규약: 프로세스_이름_부서_날짜.json (Part A). 이름은 **집계에만** 쓰고 트리에 저장하지 않는다.
    """
    dept = str(payload.get("exported_dept") or "").strip()
    author = str(payload.get("exported_by") or "").strip()
    if (not dept or not author) and filename:
        # 취합이 하위 폴더까지 재귀하면 filename 이 상대경로(과A/프로세스_...)일 수 있다 —
        # basename 만 떼어 파싱해야 폴더명이 "프로세스" 판정을 깨지 않는다.
        base = filename.replace("\\", "/").rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        parts = stem.split("_")
        # 프로세스_이름_부서_날짜 → [프로세스, 이름, 부서, 날짜]
        if len(parts) >= 4 and parts[0].startswith("프로세스"):
            author = author or parts[1]
            dept = dept or parts[2]
    return dept or "미상", author or "미상"


# `_detail_summary`·`_FREQ_LABEL` 은 **schema 로 옮겼다** — normalize 가 submit_detail 을
# 재생성해야 하는데(레코드를 고치면 취합 시점 요약이 즉시 낡는다) schema 는 excel_io 를 import 할 수
# 없기 때문이다(방향은 excel_io → schema 단방향).
_detail_summary = schema.detail_summary


def _submission_record(n: dict, dept: str, author: str = "") -> dict:
    """lv6 노드 + 제출 과 → **부서별 제출값 레코드 1건**.

    ★ 필드를 손으로 나열하지 않고 `schema.SUBMISSION_FIELDS` 를 돈다 — 상세 필드가 늘 때
      레코드 쪽만 조용히 빠지는 사고를 구조적으로 막는다(occur_pattern/events 전례).
    ★ **제출자 이름은 담는다**(원본 저장 · 출력만 마스킹 — pii.py 원칙, `updated_by` 와 동급).
      같은 과에서 두 사람이 다른 값을 내면 표에 두 줄이 뜨는데, 이름이 없으면 어느 줄이 누구 것인지
      알 수 없어 고칠 수가 없다. **파일명은 담지 않는다.** 연간공수 같은 곱한 값도 담지 않는다.
    최종 형태(빈 값 키 제거·같은 값 합산)는 `schema._norm_submissions` 가 확정하므로
    여기서는 **count=1 짜리 원본 1건**만 만든다.
    """
    rec: dict = {"dept": dept, "count": 1}
    if (author or "").strip():
        rec["author"] = author.strip()
    for k in schema.SUBMISSION_FIELDS:
        v = n.get(k)
        if isinstance(v, list):
            rec[k] = [dict(x) if isinstance(x, dict) else x for x in v]
        elif v not in (None, ""):
            rec[k] = v
    return rec


def _branches_with_detail(nodes: list[dict], nmap: dict[str, dict]) -> set[str]:
    """실제 세부업무(lv6 이상)에 닿는 가지의 노드 id 집합 = lv6·lv7 + 그 조상 전부.

    취합이 이 집합 밖 노드를 버리는 근거다. 골격(lv3~lv5)만 만든 제출을 그대로 병합하면
    아무도 채우지 않은 분류 노드가 정본 트리에 영구히 남는다.
    """
    keep: set[str] = set()
    for n in nodes:
        if n.get("level", 0) < schema.LOAD_LEVEL:
            continue
        cur: dict | None = n
        while cur is not None and cur["id"] not in keep:
            keep.add(cur["id"])
            cur = nmap.get(cur.get("parent_id", schema.ROOT_ID))
    return keep


def prune_empty_branches(data: dict) -> tuple[dict, list[dict]]:
    """세부업무(lv6)에 닿지 않는 **빈 가지**를 트리에서 제거. 반환: (정리된 트리, 제거된 노드들).

    `_branches_with_detail` 이 lv6·lv7 + 그 조상 전부를 keep 하므로 **여집합이 곧 빈 가지**다.
    취합만으로는 정본 트리에 이미 있던 빈 부문·대분류가 남아, 아무도 채우지 않은 시드 부문이
    요약 목록을 채운다.

    ★ `schema.delete_node` 를 가지마다 부르지 않는다 — 매번 children_index 를 다시 만들어
      같은 일을 N번 한다. 필터 1회 + normalize(order 재번호)가 맞다.
    """
    nodes = data.get("nodes", [])
    nmap = schema.node_map(nodes)
    keep = _branches_with_detail(nodes, nmap)
    removed = [n for n in nodes if n["id"] not in keep]
    if not removed:
        return data, []
    out = dict(data)
    out["nodes"] = [n for n in nodes if n["id"] in keep]
    return schema.normalize(out), removed


def collect_jsons(files: list[tuple[str, bytes]], current: dict) -> tuple[dict, list[dict], list[str]]:
    """여러 제출 JSON 을 **경로 기준으로 취합**한다. parse_json(단일 이어붙이기)과는 다른 연산이다.

    · 같은 이름 경로(lv3~lv6)는 **한 노드로 합친다** — parse_json 은 lv4~6 을 중복으로 남기지만
      취합은 "이 업무를 몇 명이 하는가"를 세는 게 목적이라 합친다.
    · **lv6 에 닿는 가지만 취합한다**(_branches_with_detail). 골격만 만든 제출은 통째로 제외하고
      리포트에 사유를 남긴다 — 조용히 0건 처리하면 "냈는데 왜 없지"가 된다.
    · 상세값은 **첫 제출자 승리** — 먼저 스캔된 파일 값을 쓰고, 뒤 제출자의 다른 상세는
      submit_detail(부서 기준, 이름 없음)에 모은다.
    · submit_count = 그 경로를 제출한 (부서,이름) distinct 인원수 N (lv4~6). 이름은 저장하지 않는다.

    반환: (병합트리, 파일별_리포트, 전역오류).
    리포트 = [{filename, dept, author, nodes, new, skipped, errors}] (skipped = lv6 미도달로 제외한 노드 수).
    """
    import copy

    errs: list[str] = []
    master = schema.normalize(copy.deepcopy(current))
    idx = _current_path_ids(master)                    # 경로튜플 → master 노드 id
    mmap = schema.node_map(master["nodes"])
    submitters: dict[tuple, set] = {}                  # 경로 → {(부서,이름)}
    dept_sets: dict[tuple, list[str]] = {}             # 경로 → [수행 과, ...] (순서 유지, 뒤에서 dedup)
    details: dict[tuple, list[str]] = {}               # 경로 → [부서 · 요약, ...]
    subs_recs: dict[tuple, list[tuple]] = {}           # 경로 → [(과, 이름, 제출값 레코드), ...]
    reports: list[dict] = []

    # 파일명 정렬 = 결정론적 '첫 제출자' (재실행 시 동일 결과)
    for filename, raw in sorted(files, key=lambda f: f[0]):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            reports.append({"filename": filename, "dept": "", "author": "",
                            "nodes": 0, "new": 0, "skipped": 0, "errors": f"JSON 읽기 실패: {e}"})
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
            reports.append({"filename": filename, "dept": "", "author": "",
                            "nodes": 0, "new": 0, "skipped": 0, "errors": "이 앱의 제출 JSON 이 아닙니다 (nodes 없음)"})
            continue

        # ★ 소속의 정본은 **스캔 폴더 경로**다 (첫 하위폴더=부서, 두 번째=과).
        #   봉투(exported_dept)는 개인이 상단에서 고른 값이라 틀리면 파일을 다시 받아야 하지만,
        #   폴더는 관리자가 파일을 옮기기만 하면 고쳐진다. 폴더가 없을 때만(루트 직하 파일·
        #   브라우저 다중업로드) 봉투 → 파일명 순으로 폴백한다 — 이 폴백을 지우지 말 것.
        #   _submitter_of 자체는 손대지 않는다(봉투 우선이라는 그 함수의 계약은 그대로 유효하다).
        b_dir, g_dir = _dept_from_path(filename)
        env_dept, author = _submitter_of(payload, filename)
        folder_dept = schema.canon_dept(g_dir or b_dir)
        dept = folder_dept or env_dept
        warn = ""
        if folder_dept and b_dir and g_dir:
            gp = schema.dept_parent(folder_dept)
            if gp != "미분류" and gp != schema.canon_dept(b_dir):
                # 폴더를 잘못 놓았을 수 있다 — 조용히 넘기지 않고 미리보기에 띄운다.
                # ★ errors 가 아니라 warn 이다. errors 에 넣으면 파일이 통째로 제외된다.
                warn = f"폴더 부서({b_dir})와 과의 소속 부서({gp})가 다릅니다 — 과 기준으로 집계합니다."
        try:
            incoming = schema.normalize({
                "nodes": [dict(n) for n in payload["nodes"] if isinstance(n, dict)],
                "domains": payload.get("domains") or {},
            })
        except Exception as e:
            reports.append({"filename": filename, "dept": dept, "author": author,
                            "nodes": 0, "new": 0, "skipped": 0, "errors": f"구조 오류: {e}"})
            continue

        imap = schema.node_map(incoming["nodes"])
        keep = _branches_with_detail(incoming["nodes"], imap)
        if not keep:
            reports.append({"filename": filename, "dept": dept, "author": author,
                            "nodes": len(incoming["nodes"]), "new": 0, "skipped": len(incoming["nodes"]),
                            "errors": f"세부업무(lv{schema.LOAD_LEVEL}) 없음 — 취합에서 제외"})
            continue
        new_cnt = 0
        skipped = 0
        # 얕은 레벨부터 — 부모 경로가 master 에 먼저 존재해야 자식을 매단다
        for n in sorted(incoming["nodes"], key=lambda x: x.get("level", 3)):
            path = tuple(schema.path_names(imap, n["id"])[3:])
            if not path:
                continue
            # lv6 에 닿지 않는 가지는 버린다 — 골격만 만든 제출이 정본 트리에 노드를 남기면,
            # 아무도 채우지 않은 lv4/lv5 가 영구히 쌓여 부문·대분류가 부풀어 오른다.
            if n["id"] not in keep:
                # 이미 master 에 있는 경로(손대지 않은 시드 부문 등)는 세지 않는다 —
                # 모든 제출에 똑같이 뜨는 잡음이라 "제외 N" 이 의미를 잃는다.
                if path not in idx:
                    skipped += 1
                continue
            mid = idx.get(path)
            if mid is None:
                # 경로 미존재 → 새 노드 생성. 부모는 상위 경로의 master id (lv3 은 ROOT)
                level = n.get("level", len(path) + 2)
                parent_id = schema.ROOT_ID if len(path) == 1 else idx.get(path[:-1])
                if parent_id is None:
                    continue                            # 부모를 못 찾음 (얕은순 처리라 정상 경로엔 안 옴)
                node = schema.new_node(parent_id, level, path[-1], author)
                for k in schema.DETAIL_FIELDS:          # 첫 제출자 상세값 복사
                    if k in n:
                        node[k] = (list(n[k]) if isinstance(n[k], list) else n[k])
                master["nodes"].append(node)
                mmap[node["id"]] = node
                mid = node["id"]
                idx[path] = mid
                new_cnt += 1
            # 인원 집계 — **lv6(실제 세부업무)만**. lv4/lv5(공유 골격)은 세지 않고, lv7 은 롤업 대상이라
            #   세지 않는다(LOAD_LEVEL 기준, LEVEL_MAX 아님). 골격만 여럿이 내도 부풀지 않게(위험성 검토
            #   결론): "이 세부업무를 몇 명이 하는가"만 센다.
            if n.get("level") == schema.LOAD_LEVEL:
                submitters.setdefault(path, set()).add((dept, author))
                # 수행 과 누적 — 한 업무를 여러 과가 하면 **모두** 기록해야 과별 집계에 다 잡힌다.
                # 예전엔 첫 제출자의 과만 남아, 나머지 과의 업무가 집계에서 사라졌다
                # (submit_count 는 3명이라는데 by_dept 는 한 과에만 1을 주는 모순).
                if dept:
                    dept_sets.setdefault(path, []).append(dept)
                # 과별 제출값 원본 — 대표값(첫 제출자 승리)이 덮어버리는 나머지 과의
                # 소요시간·주기·자동화·기술을 여기 보존한다. submit_detail 은 이것의 요약이라
                # 둘 다 남긴다(옛 트리·엑셀 왕복본은 submit_detail 만 갖고 있다).
                subs_recs.setdefault(path, []).append((dept, author, _submission_record(n, dept, author)))
                summ = _detail_summary(n)
                if summ:
                    details.setdefault(path, []).append(f"{dept} · {summ}")

        reports.append({"filename": filename, "dept": dept, "author": author,
                        "nodes": len(incoming["nodes"]), "new": new_cnt, "skipped": skipped,
                        "errors": "", "warn": warn})

    # 수행 과 기록 — **이번 스캔에 나온 경로는 이번 제출자 소속으로 교체한다(누적 아님).**
    #
    # ★ 예전엔 기존 값과 합집합이라 **줄어들 길이 없었다.** 제출본의 소속을 고쳐 다시 취합해도
    #   옛 과가 남아 한 업무가 두 과에 중복 계상됐고, 정정이 원천적으로 불가능했다.
    #   운영은 공유폴더 전체를 재귀 스캔하므로 **한 회차의 스캔 = 그 시점의 완전한 진실**이다.
    #   같은 업무를 여러 과가 실제로 하면 그 회차에 여러 명이 제출하므로 다중 귀속은 그대로 유지된다.
    # ★ 이번 스캔에 **없는 경로는 건드리지 않는다** — 폴더 일부만 스캔해도 나머지 귀속이 날아가지 않는다.
    for path, ds in dept_sets.items():
        mid = idx.get(path)
        node = mmap.get(mid) if mid else None
        if not node:
            continue
        new_ds: list[str] = []
        for d in ds:                              # 순서 유지 dedup (파일명 정렬 = 결정론적)
            if d not in new_ds:
                new_ds.append(d)
        node["depts"] = new_ds
        if new_ds:
            node["dept"] = new_ds[0]              # 대표 과 (카드·엑셀 단일 표시용)

    # 과별 제출값 기록 — **depts 와 정확히 같은 원칙**이다(위 블록의 근거를 그대로 따른다):
    #   ① 이번 스캔에 등장한 경로만 **교체**하고 ② 이번 스캔에 없는 경로는 건드리지 않는다.
    # 누적(합집합)으로 두면 줄어들 길이 없어, 제출본을 고쳐 재취합해도 옛 값이 영원히 남는다.
    # ★ 여기서는 (과,이름) 1명 = 레코드 1건(count=1)만 만든다. 같은 과가 **같은 값**을 낸 건
    #   맨 아래 schema.normalize 가 한 건으로 합치며 count 를 누적한다 — 합산 규칙을 두 곳에
    #   두지 않으려는 것이고, 그래서 sum(rec.count) == submit_count 가 자동으로 성립한다.
    for path, items in subs_recs.items():
        mid = idx.get(path)
        node = mmap.get(mid) if mid else None
        if not node:
            continue
        seen_sub: set = set()
        recs: list[dict] = []
        for d, a, rec in items:                   # 파일명 정렬 순 = 결정론적(재취합 멱등)
            if (d, a) in seen_sub:
                continue                          # 한 사람이 파일을 둘 낸 경우 1회만
            seen_sub.add((d, a))
            recs.append(rec)
        node["submissions"] = recs

    # 집계 결과를 노드에 기록 — N≥2 인 경로만 (혼자 한 업무는 배지 노이즈)
    for path, subs in submitters.items():
        mid = idx.get(path)
        node = mmap.get(mid) if mid else None
        if not node:
            continue
        if len(subs) >= 2:
            node["submit_count"] = str(len(subs))
            lines = details.get(path, [])
            seen: set = set()
            uniq = [ln for ln in lines if not (ln in seen or seen.add(ln))]  # 순서 유지 dedup
            node["submit_detail"] = "\n".join(uniq)

    return schema.normalize(master), reports, errs


def unknown_domain_values(data: dict) -> dict[str, list[str]]:
    """도메인 마스터에 없는 값 수집 (업로드 후 '도메인에 추가할까요?' 안내용)."""
    doms = data.get("domains", {})
    found: dict[str, set[str]] = {"dept": set(), "tech": set(), "automation_level": set(),
                                   "frequency": set(), "linked_system": set(),
                                   "ship_type": set(), "special_note": set()}
    # 다중값 노드필드 → 도메인 키 (future_tech 는 tech 도메인을 공유한다)
    list_field_dom = {"tech": "tech", "future_tech": "tech",
                      "ship_types": "ship_type", "special_note": "special_note"}
    for n in data.get("nodes", []):
        # 과별 제출값의 소속도 검사한다 — 폴더명에서 온 미등록 과가 여기에만 있을 수 있고,
        # 안 보면 "도메인에 추가할까요?" 승인 흐름을 조용히 빠져나간다.
        for r in n.get("submissions") or []:
            v = schema.canon_dept(isinstance(r, dict) and r.get("dept"))
            if v and v not in doms.get("dept", []):
                found["dept"].add(v)
        for k in ("dept", "automation_level", "frequency", "linked_system"):
            v = n.get(k)
            if k == "dept":
                # 오타·대소문자·옛 부서명 별칭은 정식표기로 맞춰 비교한다 —
                # 안 그러면 'Cedar CSU' 같은 흔들림이 매번 "도메인에 추가할까요?" 로 뜬다.
                v = schema.canon_dept(v)
            if v and v not in doms.get(k, []):
                found[k].add(v)
        # 연계시스템 다건의 system 값도 검사
        for e in n.get("linked_systems") or []:
            v = isinstance(e, dict) and e.get("system")
            if v and v not in doms.get("linked_system", []):
                found["linked_system"].add(v)
        for fld, dk in list_field_dom.items():
            for t in n.get(fld) or []:
                if t not in doms.get(dk, []):
                    found[dk].add(t)
    return {k: sorted(v) for k, v in found.items() if v}


def default_filename(prefix: str = "프로세스계층도", ext: str = "xlsx") -> str:
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M}.{ext}"
