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

import json
import re
import uuid
from datetime import datetime
from typing import Any, Final

# ── 고정 계층 (저장하지 않음, 표시·엑셀 전용) ─────────────
ROOT_ID: Final[str] = "__root__"
FIXED_LEVELS: Final[tuple[str, ...]] = ("조선", "생산", "시운전")   # lv0, lv1, lv2

# ── 편집 가능 레벨 ──────────────────────────────────────
LEVEL_MIN: Final[int] = 3
LEVEL_MAX: Final[int] = 7
LEVEL_LABELS: Final[dict[int, str]] = {
    3: "부문",
    4: "대분류",
    5: "중분류",
    6: "세부업무",
    7: "단위작업",
}

# ── 레벨별 입력 범위 ────────────────────────────────────
# lv3~lv5 는 업무를 묶는 분류 그룹이라 이름+설명만 받는다. AI 에이전트를 적용하고 담당자가
# 붙는 실체는 lv6 세부업무·lv7 단위작업이므로 상세 필드는 거기서만 입력한다(입력 폼 동일).
# 레벨이 바뀌어도 값은 지우지 않는다 — 화면에서 숨길 뿐이라 다시 lv6 으로 내리면 되살아난다.
#
# 두 기준을 분리한다:
#  · FULL_DETAIL_LEVEL(6) — **상세 폼 노출** 기준. has_detail 이 `>= 6` 이라 lv6·lv7 둘 다 폼을 받는다.
#  · LOAD_LEVEL(6)        — **부하·AI·부서 집계의 분모** 기준. lv7 값은 부모 lv6 으로 롤업하므로
#                           집계는 lv6 만 센다. has_detail(폼)을 집계 게이트로 쓰면 lv7 이 새어든다.
FULL_DETAIL_LEVEL: Final[int] = 6
LOAD_LEVEL: Final[int] = 6
DETAIL_FIELDS: Final[tuple[str, ...]] = (
    "dept", "depts", "has_ai_agent", "has_ai_future", "tech", "future_tech", "automation_level",
    "frequency", "outputs",
    "linked_system", "linked_system_detail", "linked_systems", "special_note", "ship_types",
    "work_hours", "freq_unit", "freq_count", "annual_count",
    "occur_pattern", "apply_phases", "events",
    "future_years",
)

# 부서별 제출값 레코드(node.submissions[]) 에 담는 필드.
# ★ DETAIL_FIELDS 에서 **파생**시킨다 — 손으로 나열하면 필드가 늘 때 레코드 쪽만 조용히 유실된다.
#   (occur_pattern/apply_phases/events 가 DETAIL_FIELDS 에 없어 취합이 통째로 날린 전례가 있다.)
#   dept 는 레코드의 키라서 빼고, depts 는 집계 산출물이라 뺀다.
#   desc 는 DETAIL_FIELDS 밖이지만 "왜 이 과의 숫자가 다른가"를 설명하는 유일한 텍스트라 명시 추가한다.
SUBMISSION_FIELDS: Final[tuple[str, ...]] = tuple(
    f for f in DETAIL_FIELDS if f not in ("dept", "depts")
) + ("desc",)

# ── 작업시간 ────────────────────────────────────────────
# 연간 공수 = work_hours(1회 소요시간) × annual_count(연간 횟수).
# 곱한 값은 **저장하지 않는다** — 두 원본과 어긋날 수 있으므로 annual_hours() 로만 계산한다.
# 주기를 고르면 횟수 기본값이 채워지지만, "호선별"·"수시"는 연간 횟수가 정해지지 않아 직접 입력한다.
# 향후 AI 적용 시기 선택지. 도메인이 아니라 상수다 — 연도는 사용자가 늘릴 값이 아니다.
# 범위를 넓히려면 이 배열만 고치면 된다(JS FUTURE_YEARS 트윈도 함께).
FUTURE_YEARS: Final[tuple[str, ...]] = ("2027", "2028", "2029", "2030", "2031")

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
    """상세 **입력 폼**을 받는 레벨인지 — lv6·lv7 (>= FULL_DETAIL_LEVEL). **폼 게이트 전용**이다.
    부하·AI·부서 집계의 분모는 이 함수가 아니라 `level == LOAD_LEVEL`(is_load_level) 을 써야 한다
    (안 그러면 롤업 대상인 lv7 이 분모에 새어든다)."""
    try:
        return int(level) >= FULL_DETAIL_LEVEL
    except (TypeError, ValueError):
        return False


def is_load_level(level: int) -> bool:
    """부하·AI·부서 집계의 기준(분모) 레벨인지 — lv6 만. lv7 값은 부모 lv6 으로 롤업한다."""
    try:
        return int(level) == LOAD_LEVEL
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
    "친환경실증랩": ("친환경실증랩",),
}
# 과 → 부서 역인덱스 (표시·집계 롤업용)
_DEPT_PARENT: Final[dict[str, str]] = {g: b for b, gs in DEPT_TREE.items() for g in gs}
# dept 도메인 = 과 평면 리스트 (기존 flat 소비자 전부 그대로 동작)
_DEPT_FLAT: Final[list[str]] = [g for gs in DEPT_TREE.values() for g in gs]
# 구 기본 부서 리스트 — 저장본이 이 값 그대로면 과 리스트로 1회 마이그레이션(사용자 편집분은 보존)
_OLD_DEFAULT_DEPT: Final[frozenset[str]] = frozenset(
    ["시운전1부", "시운전2부", "시운전3부", "기획운영부", "해운부", "해양사업부"])

# 옛 부서명 → 현 부서명 별칭. 조직명이 바뀐 것들만 둔다(과 이름이 아니라 **부서** 이름).
DEPT_ALIASES: Final[dict[str, str]] = {"기획운영부": "기획운영"}

# lv3 부문 이름 오타 교정 — 정규화 키(대문자·공백정리) → 정식 표기.
# 시운전3부 과 이름(CEDAR CSU / ENI CSU)을 부문으로 쓰면서 CSU 를 CUS 로 뒤집어 친 사례가 있어,
# 같은 부문이 둘로 갈렸다. 취합이 **이름 경로**로 병합하므로 이름이 갈리면 인원 집계도 갈린다.
LV3_NAME_FIXES: Final[dict[str, str]] = {
    "CEDAR CUS": "CEDAR CSU",
    "ENI CUS": "ENI CSU",
}


def _norm(v: Any) -> str:
    """비교용 정규화 키 — 앞뒤 공백 제거 + 연속공백 1칸 + 대문자.

    'Cedar  cus' / 'CEDAR CUS' 를 한 키로 모은다. 저장값은 바꾸지 않고 **조회에만** 쓴다.
    """
    return re.sub(r"\s+", " ", str(v or "").strip()).upper()


# 정규화 키 기반 역인덱스 (대소문자·공백 흔들림 흡수)
_DEPT_PARENT_N: Final[dict[str, str]] = {_norm(g): g for g in _DEPT_PARENT}
_DEPT_BRANCH_N: Final[dict[str, str]] = {_norm(b): b for b in DEPT_TREE}
_DEPT_ALIAS_N: Final[dict[str, str]] = {_norm(k): v for k, v in DEPT_ALIASES.items()}
_LV3_FIX_N: Final[dict[str, str]] = {_norm(k): v for k, v in LV3_NAME_FIXES.items()}


def canon_dept(v: str) -> str:
    """소속 값 정규화 — 과 정식표기로 맞춘다. 못 찾으면 **원문 그대로**(값 유실 금지).

    대소문자·공백 차이와 옛 부서명 별칭을 흡수한다. 부서명 자체(시운전1부 등)는
    과가 아니므로 그대로 남고, 부서 롤업은 dept_parent 가 처리한다.
    """
    raw = str(v or "").strip()
    if not raw:
        return ""
    k = _norm(raw)
    if k in _DEPT_PARENT_N:                  # 과 (오타·대소문자 흔들림 포함)
        return _DEPT_PARENT_N[k]
    if k in _DEPT_ALIAS_N:                   # 옛 부서명 → 현 부서명
        return _DEPT_ALIAS_N[k]
    if k in _DEPT_BRANCH_N:                  # 부서명 그대로 입력된 옛 데이터
        return _DEPT_BRANCH_N[k]
    return raw


def canon_lv3_name(name: str) -> str:
    """lv3 부문 이름 정규화 — 오타 교정 + **과/부서 정식표기로 통일**.

    오타(CUS→CSU)만 고치면 `Cedar CSU` 처럼 철자는 맞고 대소문자만 다른 이름이 그대로 남아,
    취합이 이름 경로로 병합할 때 `CEDAR CSU` 와 여전히 두 부문으로 갈린다.
    lv3 부문 이름이 곧 과 이름인 사례(CEDAR/ENI/ZLNG CSU)라 canon_dept 의 역인덱스를 그대로 쓴다.

    시드 부문과 충돌하지 않는다 — `해운부`·`기획운영`·`코멘더` 는 과/부서 이름이기도 하지만
    정식표기가 자기 자신이고, `선장운전` 등은 과 이름이 아니라 폴백에 걸리지 않는다.
    """
    raw = str(name or "").strip()
    if not raw:
        return raw
    k = _norm(raw)
    if k in _LV3_FIX_N:                     # 오타 (CUS → CSU)
        return _LV3_FIX_N[k]
    if k in _DEPT_PARENT_N:                 # 과 정식표기 (Cedar CSU → CEDAR CSU)
        return _DEPT_PARENT_N[k]
    if k in _DEPT_BRANCH_N:                 # 부서 정식표기
        return _DEPT_BRANCH_N[k]
    return raw                              # 그 외는 원문 보존 (유실 금지)


def dept_parent(gwa: str) -> str:
    """소속 → 부서. (표시·집계 롤업 전용, 저장은 과만.)

    옛 데이터가 **부서명**을 그대로 적어둔 경우가 많아 3단으로 찾는다:
      1) 과면 그 과의 부서   2) 값 자체가 부서면 그 부서   3) 그 외 '미분류'
    2단이 없으면 '시운전1부' 같은 옛 값이 통째로 미분류로 떨어져 부서 롤업이 무의미해진다.
    """
    k = _norm(gwa)
    if not k:
        return "미분류"
    if k in _DEPT_PARENT_N:
        return _DEPT_PARENT[_DEPT_PARENT_N[k]]
    if k in _DEPT_ALIAS_N:
        return _DEPT_ALIAS_N[k]
    if k in _DEPT_BRANCH_N:
        return _DEPT_BRANCH_N[k]
    return "미분류"


# ── 도메인 마스터 기본값 ────────────────────────────────
DEFAULT_DOMAINS: Final[dict[str, list[str]]] = {
    "dept": list(_DEPT_FLAT),   # 부서/과 = 과 평면 리스트 (2단은 DEPT_TREE + optgroup 으로 표현)
    "tech": ["LLM", "OCR", "RPA", "예측모델", "이상탐지", "BI/대시보드", "챗봇", "음성인식", "컴퓨터비전"],
    "automation_level": ["수동", "부분자동", "완전자동", "AI자동"],
    "frequency": ["일 1회", "주 1회", "월 1회", "분기", "연 1회", "호선별", "수시"],
    "linked_system": ["SAP", "NONSAP"],
    "ship_type": ["CNT", "COT", "LNG", "SHTL", "VLAC", "VLCC", "FLNG"],   # 적용 선종(다중)
    "special_note": ["SG", "DF(LNG)", "메탄올", "LPG"],                    # 특이사항(다중)
    # AI 적용으로 **세지 않을** 활용기술. 화면·엑셀 라벨은 긍정형("AI로 카운트")이지만 저장은 부정형이다.
    # ★ 포함목록(=AI로 셀 기술)이 아니라 **제외목록**인 이유:
    #   · 기본값이 [] 라 기존 저장본이 그대로 통과한다 — 마이그레이션이 무손실이고 자명하다.
    #     포함목록이면 "지금까지 전부 AI로 셌다"를 지키려고 백필 예외를 파이썬·JS 양쪽에 둬야 한다.
    #   · 새 기술이 들어오는 경로가 4개(도메인 추가·패널에서 직접 추가·취합 승인·엑셀 승인)인데,
    #     포함목록이면 그때마다 명단에 넣어줘야 하고 빠뜨리면 **AI율이 조용히 과소** 계상된다.
    #     제외목록은 명단에 없으면 AI라 현행 의미가 그대로 보존된다(동기화 지점 0).
    "tech_no_ai": [],
}

DOMAIN_LABELS: Final[dict[str, str]] = {
    "dept": "부서/과",
    "tech": "활용기술",
    "automation_level": "자동화 수준",
    "frequency": "수행 주기",
    "linked_system": "연계시스템",
    "ship_type": "적용 선종",
    "special_note": "특이사항",
    "tech_no_ai": "AI 카운트 제외 기술",
}

# 노드 필드 기본값 (누락 필드 보정용)
NODE_DEFAULTS: Final[dict[str, Any]] = {
    "name": "",
    "desc": "",
    "dept": "",
    "has_ai_agent": False,
    "tech": [],
    "automation_level": "",
    # 소속 — dept 는 **대표 과**(카드·엑셀 단일 표시용), depts 는 **이 업무를 하는 과 전부**.
    # 여러 과가 같은 업무를 제출하면 depts 에 모두 쌓이고 과별 집계가 각 과에 1씩 잡는다.
    # ★ 담당자(owner)는 제거됐다 — 제출자가 자기 일을 정의한 것이라 이름은 제출자와 중복이고,
    #   어떤 계산도 좌우하지 않았다(공통규칙 7 최소수집). normalize 가 옛 값을 지운다.
    "depts": [],
    "frequency": "",
    "outputs": "",              # 산출물
    "linked_system": "",        # (구) 연계시스템 단일 — back-compat. 신규 UI 는 linked_systems 사용
    "linked_system_detail": "", # (구) 연계시스템 추가정보 단일 — back-compat
    "linked_systems": [],       # 연계시스템 다건 — [{system, detail}, ...] (호선이벤트 events[] 식)
    "future_tech": [],          # 향후 AI 적용 기술 (다중) — 현재 활용기술(tech)과 같은 도메인
    # 향후 기술별 적용 시기 — {기술명: "2027"}. **객체 맵이라 리스트 강제변환 대상이 아니다.**
    # normalize 가 future_tech 밖 키와 FUTURE_YEARS 밖 값을 버린다(해제한 기술의 연도가 새지 않게).
    "future_years": {},
    "has_ai_future": False,     # 향후 AI 적용 (파생: future_tech 비었나)
    "special_note": [],         # 특이사항 (다중) — SG/DF(LNG)/메탄올/LPG
    "ship_types": [],           # 적용 선종 (다중) — 호선 패턴일 때만 입력
    "work_hours": "",       # 1회 소요시간 (시간, 0.5 = 30분)
    "freq_unit": "",        # 기간 단위 (일/주/월/분기/년) — 칩 택1
    "freq_count": "",       # 단위당 횟수 (예: 주 3회 → freq_count=3)
    "annual_count": "",     # 연간 횟수 (구 데이터 폴백; 신규는 freq_count×단위연간수로 파생)
    # ── 발생 패턴 (개인 배포판에서 입력, 부하 계산의 뼈대) ──
    # ★ 파이썬이 이 셋을 몰라서 **취합 때 통째로 유실되던 버그가 있었다** — DETAIL_FIELDS 에
    #   없으면 collect_jsons 가 첫 제출자 값을 복사하지 않아 "언제·몇 번 하는가"가 사라진다.
    #   work_hours 같은 숫자만 남아 부하 엔진의 입력이 반쪽이 된다.
    "occur_pattern": "",    # 상시루틴 / 호선루틴 / 호선이벤트 (JS OCCUR 와 같은 문자열)
    "apply_phases": [],     # 호선루틴: 반복 구간(복수) — TRIAL_PHASES
    "events": [],           # 호선이벤트: [{event, offset_start, offset_days}] — 줄 수 = 호선당 횟수
    # ── 취합 산출물 (메인앱 collect_jsons 가 채움; 개인 배포판은 항상 빈값) ──
    "submit_count": "",     # 이 업무(경로)를 제출한 인원수 N — (부서,이름) distinct. 이름은 저장 안 함
    "submit_detail": "",    # 제출자별 상세 요약(여러 줄, 부서 기준). 이름 미기록 (개인정보 최소수집)
    # 과별 제출값 원본 — [{dept, count, ...SUBMISSION_FIELDS}]. 대표값(첫 제출자 승리)이 덮어버린
    # 나머지 과의 소요시간·주기·자동화·기술을 여기 보존한다. submit_detail 은 이것의 텍스트 요약이다.
    # ★ DETAIL_FIELDS 에 넣지 말 것 — 취합이 자기 submissions 를 복사하는 자기참조가 생기고,
    #   has_hidden_detail() 이 취합 산출물만 가진 강등 노드를 "숨은 상세값 있음"으로 오탐한다.
    "submissions": [],
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


def _has_ai(vals: Any, no_ai: frozenset[str]) -> bool:
    """이 기술 목록이 **AI 적용**인가 — 제외목록에 없는 기술이 하나라도 있으면 참.

    예전엔 `bool(tech)` 였다. 활용기술에 SAP·엑셀매크로 같은 **현행 시스템**을 적는 일이 생기면서,
    그것만 가진 업무까지 AI 적용으로 세어 적용률이 부풀었다. 제외 여부는 도메인에서 정한다.
    """
    return any(t for t in (vals or []) if t not in no_ai)


def _norm_detail_fields(d: dict, no_ai: frozenset[str] = frozenset()) -> dict:
    """상세 필드(events·다중값 리스트·연계시스템·파생 AI)를 제자리 정규화한다.

    ★ 노드 본체와 **부서별 제출 레코드**(submissions[])가 같은 규칙을 쓰게 하려고 뽑아냈다.
      두 벌로 두면 한쪽만 고쳐져 화면·엑셀·취합이 조용히 어긋난다.
    """
    # 호선이벤트 events[] — [{event, offset_start, offset_days}]. 줄 수가 곧 호선당 횟수라
    # 빈 줄은 버린다. 형태가 깨진 값(문자열 등)은 통째로 비운다(조용히 반쪽으로 두지 않는다).
    evs = d.get("events")
    d["events"] = [
        {"event": str((e or {}).get("event") or "").strip(),
         "offset_start": str((e or {}).get("offset_start") or "").strip(),
         "offset_days": str((e or {}).get("offset_days") or "").strip()}
        for e in (evs if isinstance(evs, list) else [])
        if isinstance(e, dict) and str(e.get("event") or "").strip()
    ]
    # 다중값 리스트 필드(활용기술·향후기술·특이사항·선종·반복구간) — 쉼표문자열도 관용하고 공백 정리
    for lk in ("tech", "future_tech", "special_note", "ship_types", "apply_phases"):
        if not isinstance(d.get(lk), list):
            d[lk] = [s for s in str(d.get(lk) or "").split(",") if s.strip()]
        d[lk] = [str(t).strip() for t in d[lk] if str(t).strip()]
    # 연계시스템 다건 [{system, detail}] — 구 단일 필드(linked_system/detail)에서 1회 이관
    ls = d.get("linked_systems")
    if not isinstance(ls, list):
        ls = []
    ls = [{"system": str((e or {}).get("system") or "").strip(),
           "detail": str((e or {}).get("detail") or "").strip()}
          for e in ls if isinstance(e, dict) and ((e.get("system") or e.get("detail")))]
    if not ls and (d.get("linked_system") or d.get("linked_system_detail")):
        ls = [{"system": str(d.get("linked_system") or "").strip(),
               "detail": str(d.get("linked_system_detail") or "").strip()}]
    d["linked_systems"] = ls
    # AI 적용여부는 파생 — **제외목록에 없는 기술이 하나라도 있으면** 적용(_has_ai 주석 참조).
    # 저장형 파생을 유지하는 이유: 소비자(rollup_has_ai→stats→KPI·막대·엑셀 요약·드릴다운)가 10곳인데
    # 이 두 줄만 고치면 전원 자동으로 따라온다. 읽기 시점 계산으로 바꾸면 그 10곳 + JS 트윈을 다 손대야 한다.
    d["has_ai_agent"] = _has_ai(d["tech"], no_ai)
    d["has_ai_future"] = _has_ai(d["future_tech"], no_ai)
    # 향후 적용 시기 {기술: 연도} — **선택된 향후 기술 + 유효 연도만** 남긴다.
    #   해제한 기술의 연도가 남으면 엑셀·LLM 컨텍스트로 새고, 최댓값 표기까지 거짓이 된다.
    fy = d.get("future_years")
    d["future_years"] = {
        str(k): str(v) for k, v in (fy.items() if isinstance(fy, dict) else [])
        if str(k) in d["future_tech"] and str(v) in FUTURE_YEARS
    }
    return d


def _is_empty(v: Any) -> bool:
    """희소 저장 판정 — 빈 문자열·빈 리스트·None·False 만 '없음'으로 본다.

    `not v` 를 쓰면 **숫자 0 이 함께 지워진다**. 지금 상세필드는 전부 문자열이지만,
    나중에 숫자로 바뀌어도 조용히 값이 사라지지 않게 명시로 판정한다.
    """
    return v is None or v is False or v == "" or v == []


def _submission_sig(rec: dict) -> str:
    """레코드 값 서명 — dept/count 를 뺀 나머지의 정렬 직렬화. 같은 값 제출을 합치는 키다.

    ★ `author` 를 제외 목록에 **넣지 않는다** — 서명에 포함돼야 같은 과라도 사람이 다르면 레코드가
      갈린다. 그래야 ① 표에서 누가 낸 값인지 구분되고 ② 한 행을 편집해 다른 행과 값이 같아져도
      합쳐져 사라지지 않는다. 결과적으로 count 는 사실상 1 이 되고, 아래 병합 코드는 죽은 코드가
      아니라 **재정규화 멱등 가드**로 남는다(같은 리스트를 두 번 normalize 해도 안 늘어난다).
    """
    return json.dumps({k: v for k, v in rec.items() if k not in ("dept", "count")},
                      ensure_ascii=False, sort_keys=True, default=str)


def _norm_submissions(raw: Any, no_ai: frozenset[str] = frozenset()) -> list[dict]:
    """부서별 제출값 레코드 목록 정규화 — collect_jsons 가 채우고 여기서 형태를 확정한다.

    · dict 아닌 원소·소속 없는 레코드는 버린다 (events 와 같은 규칙 — 반쪽으로 두지 않는다).
    · 상세 필드는 노드 본체와 **같은 헬퍼**(_norm_detail_fields)를 통과시킨다.
    · **빈 값 키는 지운다**(희소 저장) — lv6 수천 개 × 과 여러 개면 트리 JSON 과
      스냅샷 50벌(prune_history keep_min)이 통째로 부푼다.
    · 같은 (과, 값서명) 레코드는 합쳐 count 를 누적한다 → 로드 시점 멱등.
    · annual_hours 같은 **곱한 값·파생값은 담지 않는다**(공통규칙). 표시할 때 계산한다.
    """
    out: list[dict] = []
    idx: dict[tuple[str, str], dict] = {}
    for e in (raw if isinstance(raw, list) else []):
        if not isinstance(e, dict):
            continue
        dept = canon_dept(e.get("dept"))
        if not dept:
            continue
        rec: dict = {}
        for k in SUBMISSION_FIELDS:                       # 헬퍼가 기대하는 키를 기본값으로 채운다
            v = e.get(k)
            if v is None:
                dv = NODE_DEFAULTS.get(k, "")
                v = list(dv) if isinstance(dv, list) else dv
            rec[k] = v
        _norm_detail_fields(rec, no_ai)      # 레코드도 노드와 **같은 AI 규칙** — 안 그러면 표·엑셀이 갈린다
        rec = {k: v for k, v in rec.items() if not _is_empty(v)}
        rec["dept"] = dept
        # ★ author 는 **명시 보존**해야 한다. 위에서 rec 를 SUBMISSION_FIELDS 로 새로 만들기 때문에
        #   그 목록에 없는 키는 정규화 1회에 증발한다 — dept/count 와 같은 취급이다.
        #   SUBMISSION_FIELDS 에 넣어 해결하면 안 된다: 그건 DETAIL_FIELDS 파생이라
        #   _submission_record 가 노드에 없는 필드를 읽게 되고 _norm_detail_fields 도 타게 된다.
        # ★ 그리고 _submission_sig 의 제외 목록에는 **넣지 않는다**(아래 주석 참조).
        #   즉 "보존할 키"와 "서명에서 뺄 키"는 서로 다른 두 목록이다.
        au = str(e.get("author") or "").strip()
        if au:
            rec["author"] = au
        try:
            cnt = max(1, int(e.get("count") or 1))
        except (TypeError, ValueError):
            cnt = 1
        key = (dept, _submission_sig(rec))
        hit = idx.get(key)
        if hit is not None:
            hit["count"] = hit.get("count", 1) + cnt      # 같은 과·같은 값 → 인원만 누적
            continue
        rec["count"] = cnt
        idx[key] = rec
        out.append(rec)
    return out


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
    # ★ 도메인 백필 **뒤에** 만든다 — 앞에서 만들면 키가 없는 옛 트리에서 빈 집합이 되어
    #   제외 설정이 로드 한 번에 무시된다. 빈 리스트([])는 "전부 AI"가 아니라 "제외 없음"이고,
    #   전부 체크 해제한 상태도 []가 아니라 tech 전체가 들어간 상태다(둘을 혼동하지 말 것).
    no_ai = frozenset(doms.get("tech_no_ai") or ())

    nodes = [n for n in data["nodes"] if isinstance(n, dict) and n.get("id")]
    for n in nodes:
        for k, dv in NODE_DEFAULTS.items():
            if k not in n or n[k] is None:
                n[k] = list(dv) if isinstance(dv, list) else dv
        _norm_detail_fields(n, no_ai)
        # 담당자 제거 — 옛 저장본에 남은 이름을 **로드 시점에** 지운다(개인정보 최소수집).
        # 저장을 누르면 파일에서도 사라진다. updated_by(저장한 사람)는 별개라 건드리지 않는다.
        n.pop("owner", None)
        # 소속 정규화 — 대소문자·공백 흔들림과 옛 부서명 별칭을 정식표기로 모은다.
        # 매핑에 없는 값(부서명 등)은 원문 그대로 남고, 부서 롤업은 dept_parent 가 처리한다.
        n["dept"] = canon_dept(n.get("dept"))
        # depts[] — 이 업무를 하는 과 전부. 순서를 지키며 중복 제거한다.
        seen_d: set[str] = set()
        ds: list[str] = []
        for v in (n.get("depts") or []):
            cv = canon_dept(v)
            if cv and cv not in seen_d:
                seen_d.add(cv)
                ds.append(cv)
        # 양방향 백필 — 옛 데이터는 dept 만 있고, 취합 산출물은 depts 만 있을 수 있다.
        if not ds and n["dept"]:
            ds = [n["dept"]]
        if ds and not n["dept"]:
            n["dept"] = ds[0]          # 대표 과 (카드·엑셀 단일 표시용)
        n["depts"] = ds
        # 과별 제출값 원본 — 취합 산출물이라 형태만 확정하고 값은 건드리지 않는다.
        n["submissions"] = _norm_submissions(n.get("submissions"), no_ai)
        # 취합 산출물 2종을 **레코드로부터 재생성**한다. 취합 시점 값을 그대로 두면 레코드를 고치는
        # 순간 낡은 요약이 화면·엑셀 `취합상세` 열·LLM 컨텍스트로 그대로 새어 나간다.
        # ★ submissions 가 비어 있으면 손대지 않는다 — 옛 트리와 엑셀 왕복본은 이 두 필드만 갖고 있다.
        if n["submissions"]:
            _tot = sum(int(r.get("count", 1) or 1) for r in n["submissions"])
            # N≥2 규칙 유지: 혼자 한 업무에 👥 배지가 뜨면 노이즈다(collect_jsons 와 같은 기준).
            n["submit_count"] = str(_tot) if _tot >= 2 else ""
            _seen_ln: set[str] = set()
            _lines: list[str] = []
            for r in n["submissions"]:
                _sm = detail_summary(r)
                if not _sm:
                    continue
                _ln = f"{r.get('dept', '')} · {_sm}"      # 이름은 넣지 않는다(detail_summary 주석)
                if _ln not in _seen_ln:
                    _seen_ln.add(_ln)
                    _lines.append(_ln)
            n["submit_detail"] = "\n".join(_lines)
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

    # lv3 부문 이름 오타 교정 — level 확정 뒤에 해야 lv3 만 정확히 걸린다.
    # 취합이 이름 경로로 병합하므로, 오타가 남으면 같은 부문이 둘로 갈려 인원 집계까지 갈린다.
    for n in nodes:
        if n["level"] == LEVEL_MIN:
            n["name"] = canon_lv3_name(n["name"])

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


def duplicate_siblings(data: dict, min_level: int = 4) -> list[dict]:
    """같은 부모 아래 **이름이 같은 형제**(중복) 목록. 하위(자손)는 보지 않는다 — 이름만 본다.

    복사(하위 포함 복제)로 생긴 동명 형제를 잡아 경고·내보내기 차단에 쓴다. 취합·붙여넣기가
    이름 경로로 병합하므로 같은 이름 형제가 남으면 두 업무가 조용히 합쳐진다. `validate` 에는
    넣지 않는다(하드 검증에 넣으면 기존 데이터 로드/저장이 거부됨) — 경고 전용.
    JS `dupSiblings` 와 규칙이 같아야 한다(twin). **기본 lv4 이상**이다 — 예전엔 lv5 였는데
    (복사가 lv5+ 에서만 되므로) 엑셀 가져오기·붙여넣기로 **lv4 동명 형제**도 생기고,
    취합이 lv4~lv6 을 이름 경로로 병합하므로 lv4 도 똑같이 위험하다. lv3(부문)은 별도 안내다.
    반환: [{parent_id, level, name, ids:[...]}] (이름·부모별 1건, count>=2).
    """
    groups: dict[tuple, list[dict]] = {}
    for n in data.get("nodes", []):
        if not isinstance(n, dict):
            continue
        try:
            lv = int(n.get("level", 0))
        except (TypeError, ValueError):
            continue
        nm = str(n.get("name") or "").strip()
        if lv < min_level or not nm:            # 빈 이름·상위 레벨은 제외
            continue
        groups.setdefault((n.get("parent_id", ROOT_ID), nm), []).append(n)
    out: list[dict] = []
    for (pid, nm), members in groups.items():
        if len(members) >= 2:
            out.append({"parent_id": pid, "level": members[0].get("level"),
                        "name": nm, "ids": [m.get("id") for m in members]})
    return out


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

    새 부모 밑에서 자손이 LEVEL_MAX 를 넘게 되면 거부한다 — lv7(단위작업) 아래로는 못 내려간다.
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


# ── 롤업 (lv6 = 자신 + lv7 자식) ─────────────────────────
# lv7 단위작업은 부하·기술을 부모 lv6 으로 롤업한다. 사용자가 lv6 에 직접 입력하든, lv7 로
# 쪼개 입력하든 같은 lv6 지표가 되도록 **가산**한다(한쪽만 채우는 워크플로 전제).

def rollup_hours(cidx: dict[str, list[dict]], node: dict) -> float:
    """lv6 유효 연간 공수 = 자신 + Σ(lv7 자식). cidx = children_index 결과."""
    h = annual_hours(node)
    for c in cidx.get(node["id"], []):
        if c.get("level") == LEVEL_MAX:                      # lv7 자식만
            h += annual_hours(c)
    return round(h, 1)


def rollup_has_ai(cidx: dict[str, list[dict]], node: dict, field: str = "has_ai_agent") -> bool:
    """lv6 AI 적용 = 자신 또는 lv7 자식 중 하나라도 기술 보유. field=has_ai_agent(현재)/has_ai_future(향후)."""
    if node.get(field):
        return True
    return any(c.get(field) for c in cidx.get(node["id"], []) if c.get("level") == LEVEL_MAX)


def rollup_techs(cidx: dict[str, list[dict]], node: dict, field: str = "tech") -> set[str]:
    """lv6 유효 기술 = 자신 ∪ lv7 자식들. field=tech(현재)/future_tech(향후).

    **집합**이라 같은 기술을 lv6 과 lv7 이 둘 다 가져도 그 lv6 은 1회만 센다
    (기술별 집계에서 한 업무가 중복 계상되지 않게).
    """
    out = {str(t).strip() for t in (node.get(field) or []) if str(t).strip()}
    for c in cidx.get(node["id"], []):
        if c.get("level") == LEVEL_MAX:
            out |= {str(t).strip() for t in (c.get(field) or []) if str(t).strip()}
    return out


def rollup_future_years(cidx: dict[str, list[dict]], node: dict) -> dict[str, str]:
    """lv6 유효 적용시기 = 자신 ∪ lv7 자식들의 {기술: 연도} 병합.

    같은 기술이 lv6·lv7 에서 다른 해면 **늦은 해**를 취한다 — 이 값이 "언제 다 끝나나"를
    말하는 것이라 빠른 해를 취하면 완료 시점을 낙관적으로 속인다.
    """
    out: dict[str, str] = {}

    def add(src: dict | None) -> None:
        for k, v in (src or {}).items():
            k, v = str(k), str(v)
            if v and (k not in out or v > out[k]):     # "2027" < "2031" — 문자열 비교로 충분(4자리 고정)
                out[k] = v

    add(node.get("future_years") if isinstance(node.get("future_years"), dict) else None)
    for c in cidx.get(node["id"], []):
        if c.get("level") == LEVEL_MAX:
            add(c.get("future_years") if isinstance(c.get("future_years"), dict) else None)
    return out


def future_year_max(cidx: dict[str, list[dict]], node: dict) -> str:
    """이 업무가 완전히 AI화되는 시점 = 적용시기(롤업)의 최댓값. 없으면 ''."""
    ys = rollup_future_years(cidx, node).values()
    return max(ys) if ys else ""


def _pct(part: int, whole: int) -> int:
    """적용률(%) — 분모 0 이면 0. 퍼센트는 파이썬이 계산한다(JS 재구현 금지 규칙)."""
    return round(part / whole * 100) if whole else 0


# ── 소속 다중 귀속 ──────────────────────────────────────
# 한 세부업무를 여러 과가 수행할 수 있다(취합이 제출한 과를 depts[] 에 모은다).
# ★ 과별 집계는 **수행 주체 기준**이라 합계가 세부업무 수보다 크다 — 단위가 다른 것이지 버그가 아니다.
#   KPI 의 detail_total(팀 단위 업무 수)과 과별 합계를 서로 검산하면 안 된다.

def depts_of(node: dict) -> list[str]:
    """이 업무를 수행하는 과 목록. 비어 있으면 ['(미지정)'] (집계 칸을 잃지 않게)."""
    ds = [str(d).strip() for d in (node.get("depts") or []) if str(d).strip()]
    if not ds:
        d = str(node.get("dept") or "").strip()      # depts 백필 전 데이터 방어
        ds = [d] if d else []
    return ds or ["(미지정)"]


def dept_groups_of(node: dict) -> list[str]:
    """수행 부서 목록 — 과를 부서로 롤업하고 **중복 제거**한다.

    같은 부서의 두 과가 하는 업무라도 "그 부서가 한다"는 한 번이다.
    (과별은 2, 부서별은 1 — 두 축의 숫자가 다른 게 정상이다.)
    """
    out: list[str] = []
    for d in depts_of(node):
        g = "(미지정)" if d == "(미지정)" else dept_parent(d)
        if g not in out:
            out.append(g)
    return out


_FREQ_LABEL: Final[dict[str, str]] = {"일": "일", "주": "주", "월": "월", "분기": "분기", "년": "년"}


def detail_summary(n: dict) -> str:
    """상세값 한 줄 요약 — 예 `소요 0.5h · 주 3회 · 부분자동 · AI`.

    ★ **이름을 넣지 않는다.** 이 문자열은 `submit_detail` 로 저장돼 `chat_context` 를 타고 LLM 으로
      나간다("컨텍스트에 updated_by 를 넣지 않는다" 와 같은 사고). 제출자 이름은 `submissions[].author`
      에만 두고 화면·엑셀에서 마스킹해 보여준다.
    (excel_io 에 있던 것을 옮겨왔다 — normalize 가 써야 하는데 schema 는 excel_io 를 못 부른다.)
    """
    parts: list[str] = []
    wh = str(n.get("work_hours") or "").strip()
    if wh:
        parts.append(f"소요 {wh}h")
    unit, cnt = str(n.get("freq_unit") or "").strip(), str(n.get("freq_count") or "").strip()
    if unit and cnt:
        parts.append(f"{_FREQ_LABEL.get(unit, unit)} {cnt}회")
    elif unit:
        parts.append(_FREQ_LABEL.get(unit, unit))
    auto = str(n.get("automation_level") or "").strip()
    if auto:
        parts.append(auto)
    if n.get("has_ai_agent"):
        parts.append("AI")
    tech = [t for t in (n.get("tech") or []) if t]
    if tech:
        parts.append("기술: " + ", ".join(tech))
    return " · ".join(parts)


def submissions_of(node: dict) -> list[dict]:
    """이 업무의 **과별 제출값 원본**. 대표값(노드 본체)이 덮어버린 나머지 과의 값이 여기 있다.

    대표값은 '첫 제출자 승리'라 3개 과가 같은 업무를 해도 소요시간이 하나만 남는다.
    과별 비교·과별 부하는 반드시 이 목록을 봐야 한다(submit_detail 은 이것의 텍스트 요약).
    """
    return [r for r in (node.get("submissions") or []) if isinstance(r, dict) and r.get("dept")]


def submission_of(node: dict, dept: str) -> dict | None:
    """특정 과가 낸 제출값 1건. 한 과가 서로 다른 값을 냈으면 **첫 레코드**(파일명 정렬 순)."""
    d = canon_dept(dept)
    for r in submissions_of(node):
        if r.get("dept") == d:
            return r
    return None


# 발생 패턴 — 값은 프론트(OCCUR)와 같은 문자열이어야 한다(twin).
OCCUR_SHIP_ROUTINE: Final[str] = "호선루틴"


def is_ship_routine(node: dict, cidx: dict[str, list[dict]] | None = None) -> bool:
    """이 lv6 그룹(자신+lv7 자식)이 호선루틴인가.

    호선루틴은 구간길이를 몰라 아직 연간화가 **보류**된 것이지 입력이 빠진 게 아니다.
    '부하 미입력'(사람이 채울 것)과 섞으면 채워도 숫자가 안 줄어 지표가 죽는다.
    """
    if node.get("occur_pattern") == OCCUR_SHIP_ROUTINE:
        return True
    for c in (cidx or {}).get(node["id"], []):
        if c.get("level") == LEVEL_MAX and c.get("occur_pattern") == OCCUR_SHIP_ROUTINE:
            return True
    return False


# ── 통계 ────────────────────────────────────────────────

def stats(data: dict) -> dict:
    """집계.

    AI·부서·자동화 지표의 분모는 **lv6(LOAD_LEVEL)만**이다. lv3~lv5 는 상세 필드를 입력하지
    않는 분류 그룹이라, lv7 은 롤업 대상이라 분모에서 뺀다(넣으면 이중 계상·적용률 왜곡).
    부하·AI 는 lv6 마다 자신+lv7 자식으로 롤업한다.
    """
    nodes = data.get("nodes", [])
    cidx = children_index(nodes)
    detail = [n for n in nodes if is_load_level(n.get("level", 0))]   # 모든 lv6 (lv7 유무 무관)
    by_level: dict[int, int] = {}
    by_dept: dict[str, int] = {}
    by_dept_group: dict[str, int] = {}     # 부서 롤업 (과 → 부서)
    by_auto: dict[str, int] = {}
    by_tech_now: dict[str, int] = {}       # 활용기술(현재)별 lv6 수 — 다중선택이라 합계 > lv6 수
    by_tech_future: dict[str, int] = {}    # 향후 AI 적용기술별 lv6 수
    # 과별 AI — 요약 화면의 "과별 현재 vs 향후" 비교 그래프 데이터. by_dept 와 같은 키를 쓴다.
    by_dept_ai_now: dict[str, int] = {}
    by_dept_ai_future: dict[str, int] = {}
    ai_yes = 0
    ai_future_yes = 0
    missing_total = 0                      # 부하 미입력 — 연간공수를 못 구하는 lv6 (호선루틴 제외)
    unresolved_total = 0                   # 호선루틴 — 입력은 됐지만 구간길이 미상이라 연간화 보류
    total_hours = 0.0
    ai_hours = 0.0
    ai_future_hours = 0.0
    for n in nodes:
        by_level[n.get("level", 0)] = by_level.get(n.get("level", 0), 0) + 1
    for n in detail:
        # ★ 과별 축은 **수행 주체 기준**이라 한 업무가 여러 과에 잡힌다(합계 > lv6 수).
        #   KPI 의 detail_total 은 팀 단위 업무 수라 단위가 다르다 — 둘을 검산하면 안 된다.
        ds = depts_of(n)
        for d in ds:
            by_dept[d] = by_dept.get(d, 0) + 1
        # 부서 롤업은 **부서 집합으로 중복 제거** — 같은 부서의 두 과가 해도 그 부서는 1회다.
        for g in dept_groups_of(n):
            by_dept_group[g] = by_dept_group.get(g, 0) + 1
        a = n.get("automation_level") or "(미지정)"
        by_auto[a] = by_auto.get(a, 0) + 1
        h = rollup_hours(cidx, n)                            # 자신 + lv7 자식
        total_hours += h
        # 부하 미입력 = 원자값이 없어 연간공수를 못 구하는 것. 호선루틴은 "보류"라 따로 센다
        # (사람이 채울 것 ≠ 나중에 trial_schedule 조인으로 풀릴 것).
        if h <= 0:
            if is_ship_routine(n, cidx):
                unresolved_total += 1
            else:
                missing_total += 1
        if rollup_has_ai(cidx, n):                           # 자신 or lv7 자식
            ai_yes += 1
            ai_hours += h
            for d in ds:
                by_dept_ai_now[d] = by_dept_ai_now.get(d, 0) + 1
        if rollup_has_ai(cidx, n, "has_ai_future"):
            ai_future_yes += 1
            ai_future_hours += h
            for d in ds:
                by_dept_ai_future[d] = by_dept_ai_future.get(d, 0) + 1
        for t in rollup_techs(cidx, n, "tech"):
            by_tech_now[t] = by_tech_now.get(t, 0) + 1
        for t in rollup_techs(cidx, n, "future_tech"):
            by_tech_future[t] = by_tech_future.get(t, 0) + 1
    return {
        "total": len(nodes),
        "detail_total": len(detail),          # lv6 세부업무 수 = AI 지표의 분모
        "by_level": dict(sorted(by_level.items())),
        "by_dept": dict(sorted(by_dept.items(), key=lambda kv: -kv[1])),
        "by_dept_group": dict(sorted(by_dept_group.items(), key=lambda kv: -kv[1])),
        "by_automation": dict(sorted(by_auto.items(), key=lambda kv: -kv[1])),
        # 기술별 — 한 업무가 기술 3개면 3칸에 각 1회. **합계 > lv6 수가 정상**(다중선택 축)
        "by_tech_now": dict(sorted(by_tech_now.items(), key=lambda kv: -kv[1])),
        "by_tech_future": dict(sorted(by_tech_future.items(), key=lambda kv: -kv[1])),
        # 과별 AI — by_dept 와 같은 키. 요약의 "과별 현재 vs 향후" 그래프가 쓴다.
        "by_dept_ai_now": by_dept_ai_now,
        "by_dept_ai_future": by_dept_ai_future,
        "missing_total": missing_total,          # 부하 미입력 (사람이 채워야 할 것)
        "unresolved_total": unresolved_total,    # 호선루틴 보류 (구간길이 조인으로 풀릴 것)
        "ai_yes": ai_yes,
        "ai_no": len(detail) - ai_yes,
        "ai_rate": _pct(ai_yes, len(detail)),
        # 향후 AI — has_ai_future(=향후기술 보유) 기준. 현재와 분모는 같고 분자만 다르다.
        "ai_future_yes": ai_future_yes,
        "ai_future_no": len(detail) - ai_future_yes,
        "ai_future_rate": _pct(ai_future_yes, len(detail)),
        # 연간 공수 — "어느 업무가 시간을 먹는가 / 자동화하면 몇 시간이 빠지는가"
        "total_hours": round(total_hours, 1),
        "ai_hours": round(ai_hours, 1),
        "ai_future_hours": round(ai_future_hours, 1),
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
