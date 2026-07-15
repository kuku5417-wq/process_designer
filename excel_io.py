"""excel_io.py — 프로세스 계층도 ↔ 엑셀 변환.

다운로드: 노드 1개 = 행 1개. 조상 이름을 lv0~lv6 컬럼에 채우고 자기 이름은 자기 레벨
컬럼에 둔다(오른쪽은 공란) — 엑셀에서 그대로 눈에 보이는 아웃라인이 되고 피벗도 된다.
lv0~lv2 는 저장 데이터에 없는 고정값이므로 이 시점에 재부착한다.

업로드: 같은 포맷을 읽어 id 로 기존 노드와 매칭하고, 부모는 lv 경로로 해석한다.
담당자 컬럼이 마스킹된 값(홍*동)이면 원본을 덮어쓰지 않는다 — 마스킹된 엑셀을 그대로
올렸을 때 이름이 파괴되는 것을 막는 안전장치다.
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

import schema
from pii import mask_name

SHEET_TREE = "계층도"
SHEET_DOMAIN = "도메인"
SHEET_SUMMARY = "요약"

LV_COLS = [f"lv{i}" for i in range(0, schema.LEVEL_MAX + 1)]      # lv0..lv6

# 엑셀 컬럼명 → 노드 필드명
FIELD_COLS: dict[str, str] = {
    "AI에이전트": "has_ai_agent",
    "활용기술": "tech",
    "부서/과": "dept",
    "자동화수준": "automation_level",
    "담당자": "owner",
    "수행주기": "frequency",
    "산출물/연계시스템": "outputs",
    "업무설명": "desc",
}

TREE_COLS: list[str] = LV_COLS + ["레벨", "이름"] + list(FIELD_COLS) + ["작성자", "수정일시", "id"]


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
    """계층도를 엑셀용 평면 표로. mask=True 면 담당자 이름을 마스킹한다."""
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
        row["자동화수준"] = n.get("automation_level", "")
        owner = n.get("owner", "")
        row["담당자"] = mask_name(owner) if (mask and owner) else owner
        row["수행주기"] = n.get("frequency", "")
        row["산출물/연계시스템"] = n.get("outputs", "")
        row["업무설명"] = n.get("desc", "")
        row["작성자"] = mask_name(n.get("updated_by", "")) if mask else n.get("updated_by", "")
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


def _summary_df(data: dict) -> pd.DataFrame:
    s = schema.stats(data)
    rows: list[dict] = [{"구분": "전체", "항목": "업무 수", "값": s["total"]},
                        {"구분": "AI 에이전트", "항목": "적용", "값": s["ai_yes"]},
                        {"구분": "AI 에이전트", "항목": "미적용", "값": s["ai_no"]}]
    for lv, c in s["by_level"].items():
        rows.append({"구분": "레벨별", "항목": f"lv{lv} ({schema.LEVEL_LABELS.get(lv, '')})", "값": c})
    for d, c in s["by_dept"].items():
        rows.append({"구분": "부서별", "항목": d, "값": c})
    for a, c in s["by_automation"].items():
        rows.append({"구분": "자동화수준별", "항목": a, "값": c})
    return pd.DataFrame(rows)


def build_xlsx(data: dict, mask: bool = True) -> bytes:
    """3시트 엑셀 bytes (계층도 / 도메인 / 요약)."""
    buf = io.BytesIO()
    tree = flatten(data, mask=mask)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tree.to_excel(xw, sheet_name=SHEET_TREE, index=False, freeze_panes=(1, 0))
        dom = _domain_df(data)
        if not dom.empty:
            dom.to_excel(xw, sheet_name=SHEET_DOMAIN, index=False, freeze_panes=(1, 0))
        _summary_df(data).to_excel(xw, sheet_name=SHEET_SUMMARY, index=False, freeze_panes=(1, 0))

        widths = {"lv0": 8, "lv1": 8, "lv2": 10, "lv3": 16, "lv4": 20, "lv5": 20, "lv6": 22,
                  "레벨": 6, "이름": 22, "AI에이전트": 11, "활용기술": 20, "부서/과": 12,
                  "자동화수준": 11, "담당자": 10, "수행주기": 10,
                  "산출물/연계시스템": 26, "업무설명": 40, "작성자": 10, "수정일시": 20, "id": 12}
        ws = xw.sheets[SHEET_TREE]
        for i, c in enumerate(tree.columns, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(c, 14)
    return buf.getvalue()


def build_json_bytes(data: dict) -> bytes:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# ── 업로드 ──────────────────────────────────────────────

def _cell(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def parse_excel(xlsx: bytes, current: dict) -> tuple[dict, list[str]]:
    """엑셀 bytes → 새 트리. (data, 오류목록). 오류가 있으면 data 는 신뢰하지 말 것.

    · id 가 있으면 기존 노드와 매칭(created_at 보존), 없으면 신규 생성
    · 부모는 lv3~lv6 경로로 해석 — 부모 행이 반드시 존재해야 한다
    · 담당자가 마스킹된 값이면 기존 원본을 유지
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

        nid = _cell(r.get("id"))
        base = old.get(nid) if nid else None
        if base is None:
            nid = schema.new_id()
        level = schema.LEVEL_MIN + len(path) - 1

        owner_in = _cell(r.get("담당자"))
        owner_old = (base or {}).get("owner", "")
        # 마스킹된 값을 그대로 올린 경우 원본 보존 (홍*동 → 홍길동 유지)
        owner = owner_old if (owner_in and owner_old and owner_in == mask_name(owner_old)) else owner_in

        tech_raw = _cell(r.get("활용기술"))
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
            "owner": owner,
            "frequency": _cell(r.get("수행주기")),
            "outputs": _cell(r.get("산출물/연계시스템")),
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


def unknown_domain_values(data: dict) -> dict[str, list[str]]:
    """도메인 마스터에 없는 값 수집 (업로드 후 '도메인에 추가할까요?' 안내용)."""
    doms = data.get("domains", {})
    found: dict[str, set[str]] = {"dept": set(), "tech": set(), "automation_level": set(), "frequency": set()}
    for n in data.get("nodes", []):
        for k in ("dept", "automation_level", "frequency"):
            v = n.get(k)
            if v and v not in doms.get(k, []):
                found[k].add(v)
        for t in n.get("tech") or []:
            if t not in doms.get("tech", []):
                found["tech"].add(t)
    return {k: sorted(v) for k, v in found.items() if v}


def default_filename(prefix: str = "프로세스계층도", ext: str = "xlsx") -> str:
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M}.{ext}"
