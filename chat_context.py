"""chat_context.py — 계층 트리를 LLM 질의용 텍스트 컨텍스트로 압축.

질의 챗봇(💬 질의 탭)이 "이 트리에 대해" 답하려면 트리를 프롬프트에 실어야 한다.
JSON 을 통째로 넣으면 토큰이 폭발하고 모델이 구조에 파묻히므로, **한 줄 = 한 세부업무**
형태의 납작한 텍스트로 줄인다.

개인정보 규칙 (공통규칙 7 — 최소수집):
  · `owner` / `updated_by` 는 **넣지 않는다.** 담당자 이름은 질의에 필요 없고,
    외부 LLM 으로 나가는 순간 최소수집 원칙 위반이다.
  · 취합 상세(`submit_detail`)는 소속 기준으로만 기록돼 있어 그대로 쓴다(이름 없음).
"""
from __future__ import annotations

import schema

# 프롬프트에 실을 컨텍스트 상한(문자). 넘으면 lv6 나열을 접고 요약 통계 위주로 낸다.
MAX_CHARS = 60_000


def _tech_str(node: dict, cidx: dict, field: str) -> str:
    t = sorted(schema.rollup_techs(cidx, node, field))
    return ", ".join(t)


def _occur_str(node: dict) -> str:
    """발생 패턴 요약 — 부하 질문("몇 시간이냐")에 답하려면 단위가 필요하다."""
    pat = node.get("occur_pattern") or ""
    unit, cnt = node.get("freq_unit") or "", node.get("freq_count") or ""
    hours = node.get("work_hours") or ""
    parts = []
    if pat:
        parts.append(pat)
    if unit and cnt:
        parts.append(f"{unit} {cnt}회")
    if hours:
        parts.append(f"1회 {hours}h")
    ah = schema.annual_hours(node)
    if ah:
        parts.append(f"연간 {ah}h")
    return " / ".join(parts)


def _summary_block(data: dict) -> str:
    s = schema.stats(data)
    lines = [
        "[전체 요약]",
        f"- 전체 노드 {s['total']}개 · 세부업무(lv6) {s['detail_total']}개 (모든 지표의 분모)",
        f"- AI 적용 현재 {s['ai_yes']}개({s['ai_rate']}%) · 향후 {s['ai_future_yes']}개({s['ai_future_rate']}%)",
        f"- 연간 공수 합계 {s['total_hours']}h (AI 적용분 {s['ai_hours']}h)",
    ]
    def _kv(title: str, m: dict, limit: int = 30) -> str:
        items = list(m.items())[:limit]
        return f"- {title}: " + ", ".join(f"{k} {v}" for k, v in items) if items else ""
    for line in (_kv("부서별", s["by_dept_group"]), _kv("과별", s["by_dept"]),
                 _kv("활용기술(현재)", s["by_tech_now"]), _kv("활용기술(향후)", s["by_tech_future"]),
                 _kv("자동화 수준", s["by_automation"])):
        if line:
            lines.append(line)
    lines.append("※ 기술 집계는 다중선택이라 합계가 세부업무 수보다 클 수 있습니다(중복 아님).")
    lines.append("※ 과별 집계도 마찬가지입니다 — 한 업무를 여러 과가 수행하면 각 과에 1회씩 잡혀,"
                 " 과별 합계는 세부업무 수와 다릅니다(세부업무 수가 팀 단위 기준).")
    lines.append(f"- 부하 미입력 {s['missing_total']}개 (연간공수 산출 불가) ·"
                 f" 호선루틴 보류 {s['unresolved_total']}개 (구간길이 미상)")
    return "\n".join(lines)


def build(data: dict, max_chars: int = MAX_CHARS) -> str:
    """트리 → 질의용 텍스트. 상한을 넘으면 세부업무 나열을 잘라내고 사유를 남긴다."""
    nodes = data.get("nodes", [])
    nmap = schema.node_map(nodes)
    cidx = schema.children_index(nodes)
    head = _summary_block(data)

    rows: list[str] = []
    for n in nodes:
        if not schema.is_load_level(n.get("level", 0)):
            continue
        path = " > ".join(schema.path_names(nmap, n["id"])[3:])
        bits = [f"경로: {path}"]
        # 수행 과 — 한 업무를 여러 과가 할 수 있다(취합이 모은 depts). 부서/과 로 적어 롤업 질문에도 답하게.
        ds = [d for d in schema.depts_of(n) if d != "(미지정)"]
        if ds:
            bits.append("수행 과: " + ", ".join(f"{schema.dept_parent(d)}/{d}" for d in ds))
        now, fut = _tech_str(n, cidx, "tech"), _tech_str(n, cidx, "future_tech")
        if now:
            bits.append(f"현재기술: {now}")
        if fut:
            bits.append(f"향후기술: {fut}")
        # 향후 적용 시기 — "언제 AI 화되나" 질문에 답할 근거. 최댓값이 완료 시점이다.
        fy = schema.rollup_future_years(cidx, n)
        if fy:
            bits.append("적용시기: " + ", ".join(f"{k} {fy[k]}" for k in sorted(fy)))
            bits.append(f"완료시점: {max(fy.values())}")
        if n.get("automation_level"):
            bits.append(f"자동화: {n['automation_level']}")
        occ = _occur_str(n)
        if occ:
            bits.append(f"부하: {occ}")
        if n.get("ship_types"):
            bits.append("선종: " + ", ".join(n["ship_types"]))
        if n.get("outputs"):
            bits.append(f"산출물: {n['outputs']}")
        # 취합 산출물 — "몇 명이 이 업무를 하는가". 소속 기준이라 이름이 없다.
        if n.get("submit_count"):
            bits.append(f"제출인원: {n['submit_count']}명")
        if n.get("submit_detail"):
            bits.append("취합상세: " + str(n["submit_detail"]).replace("\n", " | "))
        if n.get("desc"):
            bits.append(f"설명: {n['desc']}")
        rows.append(" | ".join(bits))

    body_head = f"\n\n[세부업무(lv6) 목록 — 총 {len(rows)}개]\n"
    budget = max_chars - len(head) - len(body_head)
    out: list[str] = []
    used = 0
    for r in rows:
        if used + len(r) + 1 > budget:
            out.append(f"... (컨텍스트 한도로 {len(rows) - len(out)}개 생략 — 위 [전체 요약] 수치는 전체 기준입니다)")
            break
        out.append(r)
        used += len(r) + 1
    return head + body_head + "\n".join(out)


SYSTEM_PROMPT = (
    "당신은 조선소 시운전팀의 업무 프로세스 분석을 돕는 조수입니다.\n"
    "아래 [업무 계층 데이터]만 근거로 한국어로 답하세요.\n"
    "규칙:\n"
    "- 데이터에 없는 내용은 추측하지 말고 '자료에 없습니다'라고 답하세요.\n"
    "- 숫자를 물으면 직접 세어 답하고, 근거가 된 업무 경로를 함께 보여주세요.\n"
    "- 세부업무(lv6)가 모든 집계의 기준이며, lv7 단위작업 값은 부모 lv6 으로 합산돼 있습니다.\n"
    "- 답변은 간결하게. 목록이 길면 상위 10개만 보이고 총 개수를 덧붙이세요.\n"
    "- 개인 이름은 데이터에 없습니다. 담당자를 물으면 소속(부서/과)으로 답하세요."
)


def build_prompt(data: dict, question: str, history: list[dict] | None = None) -> str:
    """최종 사용자 프롬프트 — 컨텍스트 + 직전 대화 + 이번 질문."""
    parts = ["[업무 계층 데이터]", build(data)]
    for turn in (history or [])[-4:]:      # 직전 2왕복만 — 길어지면 컨텍스트를 밀어낸다
        role = "사용자" if turn.get("role") == "user" else "조수"
        parts.append(f"\n[이전 대화 · {role}] {turn.get('text', '')}")
    parts.append(f"\n[질문] {question}")
    return "\n".join(parts)
