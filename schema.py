"""schema.py — 프로세스 계층도 데이터 모델과 트리 조작 (순수 함수, Streamlit 의존 없음).

구조: 평면 노드 배열 + parent_id. 중첩 JSON 이 아닌 이유 —
  · 드래그 이동이 parent_id/order 두 필드 수정으로 끝난다 (서브트리 절단·삽입 불필요)
  · 엑셀 변환이 DataFrame 직행이고, 스냅샷 diff 가 id 기준 set 연산으로 끝난다
평면 구조의 유일한 약점인 사이클은 would_cycle() 로 막는다.

lv0(조선)·lv1(생산)·lv2(시운전)은 노드로 저장하지 않고 FIXED_LEVELS 상수로만 둔다.
편집·삭제 대상이 아니고 필드(담당자·AI에이전트 등)가 무의미하기 때문. lv3 노드의
parent_id 는 ROOT_ID 이며, 엑셀 내보내기 시점에만 lv0~lv2 컬럼으로 재부착한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

# ── 고정 계층 (저장하지 않음, 표시·엑셀 전용) ─────────────
ROOT_ID: Final[str] = "__root__"
FIXED_LEVELS: Final[tuple[str, ...]] = ("조선", "생산", "시운전")   # lv0, lv1, lv2

# ── 편집 가능 레벨 ──────────────────────────────────────
LEVEL_MIN: Final[int] = 3
LEVEL_MAX: Final[int] = 6
LEVEL_LABELS: Final[dict[int, str]] = {
    3: "부문",
    4: "대분류",
    5: "중분류",
    6: "세부업무",
}

# ── 레벨별 입력 범위 ────────────────────────────────────
# lv3~lv5 는 업무를 묶는 분류 그룹이라 이름+설명만 받는다. AI 에이전트를 적용하고 담당자가
# 붙는 실체는 lv6 세부업무뿐이므로 상세 필드는 거기서만 입력한다.
# 레벨이 바뀌어도 값은 지우지 않는다 — 화면에서 숨길 뿐이라 다시 lv6 으로 내리면 되살아난다.
FULL_DETAIL_LEVEL: Final[int] = LEVEL_MAX
DETAIL_FIELDS: Final[tuple[str, ...]] = (
    "dept", "has_ai_agent", "tech", "automation_level", "owner", "frequency", "outputs",
    "linked_system", "linked_system_detail",
    "work_hours", "freq_unit", "freq_count", "annual_count",
)

# ── 작업시간 ────────────────────────────────────────────
# 연간 공수 = work_hours(1회 소요시간) × annual_count(연간 횟수).
# 곱한 값은 **저장하지 않는다** — 두 원본과 어긋날 수 있으므로 annual_hours() 로만 계산한다.
# 주기를 고르면 횟수 기본값이 채워지지만, "호선별"·"수시"는 연간 횟수가 정해지지 않아 직접 입력한다.
FREQ_ANNUAL: Final[dict[str, int]] = {
    "일 1회": 250,      # 근무일 기준
    "주 1회": 52,
    "월 1회": 12,
    "분기": 4,
    "연 1회": 1,
}

# 기간 단위 → 연간 발생수 (기간칩+횟수 모델). 단위와 횟수를 나눠 "주 3회" 를 표현한다.
# JS FREQ_UNITS 와 쌍둥이. 일 = 근무일 기준.
FREQ_UNITS: Final[dict[str, int]] = {"일": 250, "주": 52, "월": 12, "분기": 4, "년": 1}


def _num(v: object) -> float:
    """숫자로 못 읽으면 0 — 빈칸·문자·None 에 죽지 않는다."""
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def annual_count_of(node: dict) -> float:
    """연간 횟수. 기간단위(freq_unit)가 있으면 freq_count × 단위연간수 로 파생,
    없으면 구 데이터의 annual_count 폴백."""
    u = FREQ_UNITS.get(str(node.get("freq_unit") or ""))
    if u:
        return _num(node.get("freq_count")) * u
    return _num(node.get("annual_count"))


def annual_hours(node: dict) -> float:
    """이 업무의 연간 공수(시간) = 1회 소요시간 × 연간 횟수. 하나라도 비면 0."""
    return round(_num(node.get("work_hours")) * annual_count_of(node), 1)


def josa(word: str, pair: str = "은/는") -> str:
    """한글 받침에 따라 조사를 골라 붙인다 — "부문은" / "대분류는".

    UI 텍스트가 한국어라 레벨 이름(부문·대분류·중분류·세부업무)을 문장에 넣을 때 필요하다.
    "대분류은(는)" 같은 표기를 피한다.
    """
    a, b = pair.split("/")
    if not word:
        return word
    ch = word[-1]
    if not ("가" <= ch <= "힣"):
        return f"{word}{b}"                                  # 한글이 아니면 받침 없는 형태
    return f"{word}{a}" if (ord(ch) - 0xAC00) % 28 else f"{word}{b}"


def has_detail(level: int) -> bool:
    """상세 필드(AI·기술·부서·담당자 등)를 입력하는 레벨인지."""
    try:
        return int(level) >= FULL_DETAIL_LEVEL
    except (TypeError, ValueError):
        return False


def has_hidden_detail(node: dict) -> bool:
    """상위 레벨인데 상세 값이 남아 있는지 (lv6 에서 승격된 카드)."""
    if has_detail(node.get("level", 0)):
        return False
    return any(node.get(f) for f in DETAIL_FIELDS)

# lv3 초기 시드 (최초 1회, 이후 사용자 편집이 정본).
# ★ id 를 결정론적 고정값으로 둔다 — 개인 배포용 standalone 과 메인앱이 **같은 id** 로 부문을
#   깔아야, 개인이 만든 파일을 취합할 때 기존 부문에 자동으로 붙고 중복이 생기지 않는다.
#   랜덤 uuid 로 두면 사람마다 "선장운전" id 가 달라져 부문이 인원수만큼 쌓인다.
SEED_LV3: Final[tuple[tuple[str, str], ...]] = (
    ("lv3_seonjang", "선장운전"),
    ("lv3_jeonjang", "전장운전"),
    ("lv3_gijang", "기장운전"),
    ("lv3_gihoek", "기획운영"),
    ("lv3_gongtong", "공통업무"),
    ("lv3_haeun", "해운부"),
    ("lv3_commander", "코멘더"),
    ("lv3_hy_util", "해양-유틸/프로세스"),
    ("lv3_hy_safety", "해양-안전"),
    ("lv3_hy_elec", "해양-전계장"),
)

# ── 부서/과 2단 구조 (부서 → 과) ─────────────────────────
# 노드에는 **과(최말단)만** 저장하고, 부서는 이 매핑으로 자동 표시·집계한다(dept_parent).
# 조직 구조라 자주 안 바뀌므로 상수로 둔다. frontend/index.html 의 DEPT_TREE 와 **완전히 같아야 한다**(twin).
DEPT_TREE: Final[dict[str, tuple[str, ...]]] = {
    "시운전1부": ("기장운전1과", "선장운전1과", "전장운전1과"),
    "시운전2부": ("기장운전2과", "선장운전2과", "전장운전2과"),
    "시운전3부": ("ZLNG CSU", "ENI CSU", "CEDAR CSU", "운영"),
    "안벽의장": ("안벽의장1과", "안벽의장2과", "시운전과"),
    "기획운영": ("기획운영",),
    "시운전기술": ("LNG설비운영과", "코멘더"),
    "해운부": ("해운1과", "해운2과"),
}
# 과 → 부서 역인덱스 (표시·집계 롤업용)
_DEPT_PARENT: Final[dict[str, str]] = {g: b for b, gs in DEPT_TREE.items() for g in gs}
# dept 도메인 = 과 평면 리스트 (기존 flat 소비자 전부 그대로 동작)
_DEPT_FLAT: Final[list[str]] = [g for gs in DEPT_TREE.values() for g in gs]
# 구 기본 부서 리스트 — 저장본이 이 값 그대로면 과 리스트로 1회 마이그레이션(사용자 편집분은 보존)
_OLD_DEFAULT_DEPT: Final[frozenset[str]] = frozenset(
    ["시운전1부", "시운전2부", "시운전3부", "기획운영부", "해운부", "해양사업부"])


def dept_parent(gwa: str) -> str:
    """과 → 부서. 매핑에 없으면 '미분류'. (표시·집계 롤업 전용, 저장은 과만.)"""
    return _DEPT_PARENT.get(str(gwa or "").strip(), "미분류")


# ── 도메인 마스터 기본값 ────────────────────────────────
DEFAULT_DOMAINS: Final[dict[str, list[str]]] = {
    "dept": list(_DEPT_FLAT),   # 부서/과 = 과 평면 리스트 (2단은 DEPT_TREE + optgroup 으로 표현)
    "tech": ["LLM", "OCR", "RPA", "예측모델", "이상탐지", "BI/대시보드", "챗봇", "음성인식", "컴퓨터비전"],
    "automation_level": ["수동", "부분자동", "완전자동", "AI자동"],
    "frequency": ["일 1회", "주 1회", "월 1회", "분기", "연 1회", "호선별", "수시"],
    "linked_system": ["SAP", "NONSAP"],
}

DOMAIN_LABELS: Final[dict[str, str]] = {
    "dept": "부서/과",
    "tech": "활용기술",
    "automation_level": "자동화 수준",
    "frequency": "수행 주기",
    "linked_system": "연계시스템",
}

# 노드 필드 기본값 (누락 필드 보정용)
NODE_DEFAULTS: Final[dict[str, Any]] = {
    "name": "",
    "desc": "",
    "dept": "",
    "has_ai_agent": False,
    "tech": [],
    "automation_level": "",
    "owner": "",
    "frequency": "",
    "outputs": "",              # 산출물
    "linked_system": "",        # 연계시스템 — 도메인 선택 (SAP/NONSAP 등)
    "linked_system_detail": "", # 연계시스템 추가정보 (자유 텍스트)
    "work_hours": "",       # 1회 소요시간 (시간, 0.5 = 30분)
    "freq_unit": "",        # 기간 단위 (일/주/월/분기/년) — 칩 택1
    "freq_count": "",       # 단위당 횟수 (예: 주 3회 → freq_count=3)
    "annual_count": "",     # 연간 횟수 (구 데이터 폴백; 신규는 freq_count×단위연간수로 파생)
    # ── 취합 산출물 (메인앱 collect_jsons 가 채움; 개인 배포판은 항상 빈값) ──
    "submit_count": "",     # 이 업무(경로)를 제출한 인원수 N — (부서,이름) distinct. 이름은 저장 안 함
    "submit_detail": "",    # 제출자별 상세 요약(여러 줄, 부서 기준). 이름 미기록 (개인정보 최소수집)
}

SCHEMA_VERSION: Final[int] = 1


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return "n_" + uuid.uuid4().hex[:8]


# ── 노드 생성 / 부트스트랩 ──────────────────────────────

def new_node(parent_id: str, level: int, name: str, author: str) -> dict:
    ts = now_iso()
    return {
        "id": new_id(),
        "parent_id": parent_id,
        "level": int(level),
        "order": 0,
        **{k: (list(v) if isinstance(v, list) else v) for k, v in NODE_DEFAULTS.items()},
        "name": name,
        "created_at": ts,
        "updated_at": ts,
        "updated_by": author,
    }


def bootstrap(author: str = "system") -> dict:
    """최초 실행 / 파일 손상 시의 기본 트리 (lv3 부문 시드).

    시드 id 는 SEED_LV3 의 고정값을 쓴다 — 개인 배포판과 id 가 같아야 취합이 된다.
    """
    nodes: list[dict] = []
    for i, (nid, name) in enumerate(SEED_LV3):
        n = new_node(ROOT_ID, 3, name, author)
        n["id"] = nid
        n["order"] = i
        nodes.append(n)
    return {
        "schema_version": SCHEMA_VERSION,
        "rev": 0,
        "updated_at": now_iso(),
        "updated_by": author,
        "nodes": nodes,
        "domains": {k: list(v) for k, v in DEFAULT_DOMAINS.items()},
    }


# ── 인덱스 / 조회 ───────────────────────────────────────

def node_map(nodes: list[dict]) -> dict[str, dict]:
    return {n["id"]: n for n in nodes}


def children_index(nodes: list[dict]) -> dict[str, list[dict]]:
    """parent_id -> order 정렬된 자식 목록. 컬럼 렌더 시 1회 빌드해 O(1) 조회."""
    idx: dict[str, list[dict]] = {}
    for n in nodes:
        idx.setdefault(n.get("parent_id", ROOT_ID), []).append(n)
    for lst in idx.values():
        lst.sort(key=lambda n: (n.get("order", 0), n.get("name", "")))
    return idx


def children(data: dict, parent_id: str) -> list[dict]:
    return children_index(data["nodes"]).get(parent_id, [])


def ancestors(nmap: dict[str, dict], node_id: str) -> list[dict]:
    """루트에 가까운 순서로 조상 목록 (자기 자신 제외). 사이클이 있어도 멈춘다."""
    out: list[dict] = []
    seen: set[str] = set()
    cur = nmap.get(node_id)
    while cur is not None:
        pid = cur.get("parent_id", ROOT_ID)
        if pid == ROOT_ID or pid in seen:
            break
        seen.add(pid)
        parent = nmap.get(pid)
        if parent is None:
            break
        out.append(parent)
        cur = parent
    out.reverse()
    return out


def descendants(idx: dict[str, list[dict]], node_id: str) -> list[dict]:
    """자손 전체 (DFS). 사이클이 있어도 방문 집합으로 멈춘다."""
    out: list[dict] = []
    seen: set[str] = set()
    stack = list(idx.get(node_id, []))
    while stack:
        n = stack.pop()
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        out.append(n)
        stack.extend(idx.get(n["id"], []))
    return out


def would_cycle(nmap: dict[str, dict], node_id: str, new_parent_id: str) -> bool:
    """new_parent_id 가 node_id 자신이거나 그 자손이면 True (이동 금지)."""
    if new_parent_id == node_id:
        return True
    if new_parent_id == ROOT_ID:
        return False
    cur = nmap.get(new_parent_id)
    seen: set[str] = set()
    while cur is not None:
        if cur["id"] == node_id:
            return True
        pid = cur.get("parent_id", ROOT_ID)
        if pid == ROOT_ID or pid in seen:
            return False
        seen.add(pid)
        cur = nmap.get(pid)
    return False


def path_names(nmap: dict[str, dict], node_id: str) -> list[str]:
    """lv0~자기자신 까지의 이름 경로 (고정 3단 포함)."""
    node = nmap.get(node_id)
    if node is None:
        return list(FIXED_LEVELS)
    return list(FIXED_LEVELS) + [a["name"] for a in ancestors(nmap, node_id)] + [node["name"]]


# ── 정규화 / 검증 ───────────────────────────────────────

def renumber(data: dict, parent_id: str) -> None:
    """형제 그룹의 order 를 0..n-1 로 재번호 (float gap 방식 미사용 — drift 방지)."""
    sibs = [n for n in data["nodes"] if n.get("parent_id") == parent_id]
    sibs.sort(key=lambda n: (n.get("order", 0), n.get("name", "")))
    for i, n in enumerate(sibs):
        n["order"] = i


def normalize(data: dict) -> dict:
    """결측 필드 보정 + level 재계산 + order 재번호 + 고아 노드 구제.

    level 은 파생값이지만 컬럼 필터를 O(1) 로 만들려고 denormalize 저장한다.
    로드/저장 시 parent.level+1 로 재계산해 정합성을 보장한다.
    """
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("rev", 0)
    data.setdefault("nodes", [])
    data.setdefault("updated_at", now_iso())
    data.setdefault("updated_by", "")

    doms = data.setdefault("domains", {})
    # dept 2단 마이그레이션: 저장본의 dept 가 **구 기본 부서 리스트 그대로**면 과 리스트로 교체한다.
    #   (사용자가 손댄 값이면 건드리지 않는다 — 정확히 구 기본값일 때만 1회 갈아끼움.)
    cur_dept = doms.get("dept")
    if isinstance(cur_dept, list) and cur_dept and set(cur_dept) == _OLD_DEFAULT_DEPT:
        doms["dept"] = list(_DEPT_FLAT)
    for k, v in DEFAULT_DOMAINS.items():
        cur = doms.get(k)
        if not isinstance(cur, list):
            doms[k] = list(v)
        else:
            doms[k] = [str(x).strip() for x in cur if str(x).strip()]

    nodes = [n for n in data["nodes"] if isinstance(n, dict) and n.get("id")]
    for n in nodes:
        for k, dv in NODE_DEFAULTS.items():
            if k not in n or n[k] is None:
                n[k] = list(dv) if isinstance(dv, list) else dv
        if not isinstance(n.get("tech"), list):
            n["tech"] = [s for s in str(n.get("tech") or "").split(",") if s.strip()]
        n["tech"] = [str(t).strip() for t in n["tech"] if str(t).strip()]
        n["has_ai_agent"] = bool(n.get("has_ai_agent"))
        n["name"] = str(n.get("name") or "").strip()
        n.setdefault("parent_id", ROOT_ID)
        n.setdefault("created_at", now_iso())
        n.setdefault("updated_at", n["created_at"])
        n.setdefault("updated_by", "")
    data["nodes"] = nodes

    nmap = node_map(nodes)
    # 부모가 사라진 고아는 삭제하지 않고 ROOT 로 끌어올려 데이터 손실을 막는다
    for n in nodes:
        pid = n["parent_id"]
        if pid != ROOT_ID and pid not in nmap:
            n["parent_id"] = ROOT_ID

    # 사이클 절단: 조상을 따라가다 자기 자신을 만나면 ROOT 로
    for n in nodes:
        seen: set[str] = {n["id"]}
        cur = nmap.get(n["parent_id"])
        while cur is not None:
            if cur["id"] in seen:
                n["parent_id"] = ROOT_ID
                break
            seen.add(cur["id"])
            cur = nmap.get(cur.get("parent_id", ROOT_ID))

    # level 재계산 (깊이 기준) + 범위 클램프
    for n in nodes:
        depth = len(ancestors(nmap, n["id"])) + LEVEL_MIN
        n["level"] = max(LEVEL_MIN, min(LEVEL_MAX, depth))

    for pid in {n["parent_id"] for n in nodes} | {ROOT_ID}:
        renumber(data, pid)
    return data


def validate(data: dict) -> list[str]:
    """구조 오류 목록 (빈 리스트 = 정상). 화면 배너에 그대로 띄운다."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["최상위가 객체(dict)가 아닙니다."]
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return ["nodes 가 배열이 아닙니다."]

    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"중복 id: {', '.join(sorted(str(d) for d in dup))}")

    nmap = node_map([n for n in nodes if isinstance(n, dict) and n.get("id")])
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            errs.append("id 가 없는 노드가 있습니다.")
            continue
        pid = n.get("parent_id", ROOT_ID)
        if pid != ROOT_ID and pid not in nmap:
            errs.append(f"[{n.get('name', n['id'])}] 부모 노드({pid})가 없습니다.")
        lv = n.get("level")
        if not isinstance(lv, int) or not (LEVEL_MIN <= lv <= LEVEL_MAX):
            errs.append(f"[{n.get('name', n['id'])}] level 값이 {LEVEL_MIN}~{LEVEL_MAX} 범위를 벗어났습니다: {lv}")
        if not str(n.get("name") or "").strip():
            errs.append(f"[{n['id']}] 이름이 비어 있습니다.")

    for n in nodes:
        if isinstance(n, dict) and n.get("id") and would_cycle(nmap, n["id"], n.get("parent_id", ROOT_ID)):
            errs.append(f"[{n.get('name', n['id'])}] 부모-자식 관계가 순환합니다.")
    return errs


# ── 변경 조작 ───────────────────────────────────────────

def add_node(data: dict, parent_id: str, level: int, author: str, name: str = "새 업무") -> str:
    n = new_node(parent_id, level, name, author)
    sibs = [x for x in data["nodes"] if x.get("parent_id") == parent_id]
    n["order"] = len(sibs)
    data["nodes"].append(n)
    renumber(data, parent_id)
    return n["id"]


def update_node(data: dict, node_id: str, fields: dict, author: str) -> bool:
    n = node_map(data["nodes"]).get(node_id)
    if n is None:
        return False
    for k, v in fields.items():
        if k in ("id", "parent_id", "level", "order", "created_at"):
            continue          # 구조 필드는 전용 함수로만 변경
        n[k] = v
    n["updated_at"] = now_iso()
    n["updated_by"] = author
    return True


def delete_node(data: dict, node_id: str) -> int:
    """노드와 그 자손 전부 삭제. 삭제된 노드 수 반환."""
    idx = children_index(data["nodes"])
    victims = {node_id} | {d["id"] for d in descendants(idx, node_id)}
    nmap = node_map(data["nodes"])
    parent_id = nmap[node_id]["parent_id"] if node_id in nmap else ROOT_ID
    data["nodes"] = [n for n in data["nodes"] if n["id"] not in victims]
    renumber(data, parent_id)
    return len(victims)


def _cascade_levels(data: dict, node_id: str) -> None:
    """node_id 이하 자손의 level 을 부모 기준으로 다시 매긴다."""
    nmap = node_map(data["nodes"])
    idx = children_index(data["nodes"])
    stack = [node_id]
    while stack:
        cur_id = stack.pop()
        cur = nmap.get(cur_id)
        if cur is None:
            continue
        pid = cur["parent_id"]
        cur["level"] = LEVEL_MIN if pid == ROOT_ID else min(LEVEL_MAX, nmap[pid]["level"] + 1)
        stack.extend(c["id"] for c in idx.get(cur_id, []))


def max_depth_below(data: dict, node_id: str) -> int:
    """node_id 아래로 몇 단계까지 자손이 있는지 (자손 없으면 0)."""
    idx = children_index(data["nodes"])
    nmap = node_map(data["nodes"])
    base = nmap[node_id]["level"] if node_id in nmap else LEVEL_MIN
    desc = descendants(idx, node_id)
    if not desc:
        return 0
    return max(d["level"] for d in desc) - base


def apply_move(data: dict, node_id: str, new_parent_id: str, author: str) -> tuple[bool, str]:
    """부모 변경. (성공여부, 메시지) 반환.

    새 부모 밑에서 자손이 LEVEL_MAX 를 넘게 되면 거부한다 — lv6 아래로는 못 내려간다.
    """
    nmap = node_map(data["nodes"])
    if node_id not in nmap:
        return False, "노드를 찾을 수 없습니다."
    if new_parent_id != ROOT_ID and new_parent_id not in nmap:
        return False, "대상 부모를 찾을 수 없습니다."
    node = nmap[node_id]
    if node["parent_id"] == new_parent_id:
        return False, ""
    if would_cycle(nmap, node_id, new_parent_id):
        return False, "자기 자신이나 하위 업무 밑으로는 옮길 수 없습니다."

    new_level = LEVEL_MIN if new_parent_id == ROOT_ID else nmap[new_parent_id]["level"] + 1
    if new_level > LEVEL_MAX:
        return False, f"lv{LEVEL_MAX} 아래로는 더 내려갈 수 없습니다."
    if new_level + max_depth_below(data, node_id) > LEVEL_MAX:
        return False, f"하위 업무까지 옮기면 lv{LEVEL_MAX} 를 넘습니다. 하위를 먼저 정리해 주세요."

    old_parent = node["parent_id"]
    node["parent_id"] = new_parent_id
    node["order"] = len([x for x in data["nodes"] if x.get("parent_id") == new_parent_id and x["id"] != node_id])
    node["updated_at"] = now_iso()
    node["updated_by"] = author
    _cascade_levels(data, node_id)
    renumber(data, old_parent)
    renumber(data, new_parent_id)
    return True, ""


def apply_reorder(data: dict, parent_id: str, ordered_ids: list[str], author: str) -> bool:
    """형제 순서 재지정. ordered_ids 가 정본 — DOM 과 서버의 drift 를 원천 차단한다."""
    nmap = node_map(data["nodes"])
    pos = {nid: i for i, nid in enumerate(ordered_ids)}
    touched = False
    for n in data["nodes"]:
        if n.get("parent_id") != parent_id:
            continue
        if n["id"] in pos and n.get("order") != pos[n["id"]]:
            n["order"] = pos[n["id"]]
            touched = True
    if touched:
        renumber(data, parent_id)
        for nid in ordered_ids:
            if nid in nmap:
                nmap[nid]["updated_at"] = now_iso()
                nmap[nid]["updated_by"] = author
    return touched


def move_sibling(data: dict, node_id: str, delta: int, author: str) -> bool:
    """형제 그룹 안에서 위/아래로 한 칸 이동 (폴백 UI 의 ▲▼ 버튼용)."""
    nmap = node_map(data["nodes"])
    if node_id not in nmap:
        return False
    pid = nmap[node_id]["parent_id"]
    sibs = children_index(data["nodes"]).get(pid, [])
    ids = [s["id"] for s in sibs]
    i = ids.index(node_id)
    j = i + delta
    if not (0 <= j < len(ids)):
        return False
    ids[i], ids[j] = ids[j], ids[i]
    return apply_reorder(data, pid, ids, author)


# ── 통계 ────────────────────────────────────────────────

def stats(data: dict) -> dict:
    """집계.

    AI·부서·자동화 지표의 분모는 **lv6 세부업무만**이다. lv3~lv5 는 상세 필드를 입력하지
    않는 분류 그룹이라 분모에 넣으면 전부 "미적용"으로 잡혀 적용률이 왜곡된다.
    """
    nodes = data.get("nodes", [])
    detail = [n for n in nodes if has_detail(n.get("level", 0))]
    by_level: dict[int, int] = {}
    by_dept: dict[str, int] = {}
    by_dept_group: dict[str, int] = {}     # 부서 롤업 (과 → 부서)
    by_auto: dict[str, int] = {}
    ai_yes = 0
    total_hours = 0.0
    ai_hours = 0.0
    for n in nodes:
        by_level[n.get("level", 0)] = by_level.get(n.get("level", 0), 0) + 1
    for n in detail:
        d = n.get("dept") or "(미지정)"
        by_dept[d] = by_dept.get(d, 0) + 1
        g = dept_parent(d) if n.get("dept") else "(미지정)"
        by_dept_group[g] = by_dept_group.get(g, 0) + 1
        a = n.get("automation_level") or "(미지정)"
        by_auto[a] = by_auto.get(a, 0) + 1
        h = annual_hours(n)
        total_hours += h
        if n.get("has_ai_agent"):
            ai_yes += 1
            ai_hours += h
    return {
        "total": len(nodes),
        "detail_total": len(detail),          # lv6 세부업무 수 = AI 지표의 분모
        "by_level": dict(sorted(by_level.items())),
        "by_dept": dict(sorted(by_dept.items(), key=lambda kv: -kv[1])),
        "by_dept_group": dict(sorted(by_dept_group.items(), key=lambda kv: -kv[1])),
        "by_automation": dict(sorted(by_auto.items(), key=lambda kv: -kv[1])),
        "ai_yes": ai_yes,
        "ai_no": len(detail) - ai_yes,
        # 연간 공수 — "어느 업무가 시간을 먹는가 / 자동화하면 몇 시간이 빠지는가"
        "total_hours": round(total_hours, 1),
        "ai_hours": round(ai_hours, 1),
    }


def diff(old: dict, new: dict) -> dict:
    """두 트리의 차이 요약 (스냅샷 복원 미리보기·엑셀 업로드 미리보기 공용)."""
    o = node_map(old.get("nodes", []))
    n = node_map(new.get("nodes", []))
    added = [n[i] for i in n.keys() - o.keys()]
    removed = [o[i] for i in o.keys() - n.keys()]
    changed = []
    for i in o.keys() & n.keys():
        a, b = o[i], n[i]
        keys = set(NODE_DEFAULTS) | {"parent_id", "order"}
        if any(a.get(k) != b.get(k) for k in keys):
            changed.append(b)
    return {"added": added, "removed": removed, "changed": changed}
