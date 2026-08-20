"""_smoke.py — schema/store 회귀검증 (Streamlit 없이 실행).

임시 폴더를 PROCESS_DATA_PATH 로 지정해 실제 파일 저장까지 검증한다.
사용: python _smoke.py
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pd_smoke_"))
os.environ["PROCESS_DATA_PATH"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import path_config as pc      # noqa: E402
import schema                 # noqa: E402
import store                  # noqa: E402
import excel_io               # noqa: E402
import pii                    # noqa: E402

_fails: list[str] = []
_n = 0


def ck(cond: bool, label: str) -> None:
    global _n
    _n += 1
    if cond:
        print(f"[PASS] {label}")
    else:
        print(f"[FAIL] {label}")
        _fails.append(label)


def main() -> int:
    pc.invalidate_cache()
    ck(pc.get_process_dir() == _TMP, f"PROCESS_DATA_PATH 반영: {pc.get_process_dir()}")

    # 1. bootstrap
    d = schema.bootstrap("tester")
    ck(len(d["nodes"]) == len(schema.SEED_LV3), f"bootstrap lv3 시드 {len(d['nodes'])}개")
    ck(all(n["level"] == 3 for n in d["nodes"]), "시드 노드 level=3")
    ck(schema.validate(d) == [], "bootstrap validate 통과")

    # 2. add / 계층
    lv3 = d["nodes"][0]["id"]
    lv4 = schema.add_node(d, lv3, 4, "tester", "항해장비 시운전")
    lv5 = schema.add_node(d, lv4, 5, "tester", "레이더 점검")
    lv6 = schema.add_node(d, lv5, 6, "tester", "안테나 회전 확인")
    d = schema.normalize(d)
    nmap = schema.node_map(d["nodes"])
    ck(nmap[lv6]["level"] == 6, "4단 추가 후 level=6 자동 계산")
    ck([a["id"] for a in schema.ancestors(nmap, lv6)] == [lv3, lv4, lv5], "ancestors 경로")
    ck(schema.path_names(nmap, lv4)[:3] == list(schema.FIXED_LEVELS), "path_names 고정 3단 부착")

    # 3. lv6 아래 추가 거부 / 사이클 거부
    ok, msg = schema.apply_move(d, lv3, lv6, "tester")
    ck(not ok, f"자손 밑으로 이동 거부: {msg}")
    ok, msg = schema.apply_move(d, lv3, lv3, "tester")
    ck(not ok, "자기 자신 밑으로 이동 거부")
    lv3b = d["nodes"][1]["id"]
    ok, msg = schema.apply_move(d, lv4, lv3b, "tester")
    ck(ok, "lv4 를 다른 lv3 밑으로 이동 성공")
    ck(schema.node_map(d["nodes"])[lv6]["level"] == 6, "이동 후 자손 level 캐스케이드 유지")

    # 4. 깊이 초과 거부 — lv4 서브트리(3단 깊이)를 lv5 밑으로
    deep = schema.add_node(d, lv3, 4, "tester", "임시")
    deep5 = schema.add_node(d, deep, 5, "tester", "임시5")
    d = schema.normalize(d)
    ok, msg = schema.apply_move(d, lv4, deep5, "tester")
    ck(not ok and f"lv{schema.LEVEL_MAX}" in msg, f"깊이 초과 이동 거부: {msg}")

    # 5. reorder
    ids = [c["id"] for c in schema.children(d, schema.ROOT_ID)]
    rev_ids = list(reversed(ids))
    schema.apply_reorder(d, schema.ROOT_ID, rev_ids, "tester")
    ck([c["id"] for c in schema.children(d, schema.ROOT_ID)] == rev_ids, "apply_reorder 순서 반영")
    schema.move_sibling(d, rev_ids[0], 1, "tester")
    ck(schema.children(d, schema.ROOT_ID)[1]["id"] == rev_ids[0], "move_sibling ▼ 한 칸")

    # 6. update / delete 캐스케이드
    schema.update_node(d, lv6, {"has_ai_agent": True, "tech": ["LLM", "OCR"], "outputs": "보고서"}, "tester")
    ck(schema.node_map(d["nodes"])[lv6]["has_ai_agent"] is True, "update_node 필드 반영")
    schema.update_node(d, lv6, {"level": 99, "parent_id": "x"}, "tester")
    ck(schema.node_map(d["nodes"])[lv6]["level"] == 6, "update_node 가 구조 필드를 무시")
    before = len(d["nodes"])
    removed = schema.delete_node(d, lv4)
    ck(removed == 3 and len(d["nodes"]) == before - 3, f"delete_node 자손 캐스케이드 {removed}개")

    # 6-1. 레벨별 입력 범위 — 상세는 lv6 만
    ck(not any(schema.has_detail(lv) for lv in (3, 4, 5)), "lv3~lv5 는 상세 필드 없음(분류 그룹)")
    ck(schema.has_detail(6), "lv6 은 상세 필드 입력 대상")
    ck(not schema.has_detail(None) and not schema.has_detail("x"), "has_detail 이 잘못된 값에 안 죽음")

    # 6-2. 레벨이 바뀌어도 상세 값은 보존된다 (숨김만)
    p3 = d["nodes"][0]["id"]
    w4 = schema.add_node(d, p3, 4, "tester", "보존확인 대분류")
    w5 = schema.add_node(d, w4, 5, "tester", "보존확인 중분류")
    w6 = schema.add_node(d, w5, 6, "tester", "보존확인 세부업무")
    d = schema.normalize(d)
    schema.update_node(d, w6, {"has_ai_agent": True, "tech": ["LLM"], "outputs": "보고서",
                               "dept": "시운전1부", "automation_level": "부분자동",
                               "frequency": "주 1회", "outputs": "보고서"}, "tester")
    ck(schema.node_map(d["nodes"])[w6]["level"] == 6, "보존확인 노드 lv6")
    ck(not schema.has_hidden_detail(schema.node_map(d["nodes"])[w6]), "lv6 은 숨은 값이 아님")

    ok, msg = schema.apply_move(d, w6, w4, "tester")      # lv6 -> lv5 로 승격
    n6 = schema.node_map(d["nodes"])[w6]
    ck(ok and n6["level"] == 5, f"lv6 을 lv5 로 승격: {msg}")
    ck(n6["outputs"] == "보고서" and n6["tech"] == ["LLM"] and n6["has_ai_agent"] is True,
       "승격해도 상세 값 보존 (삭제 아님)")
    ck(schema.has_hidden_detail(n6), "승격된 카드에 숨은 상세 값이 있음을 감지")

    ok, _ = schema.apply_move(d, w6, w5, "tester")        # 다시 lv6 으로
    n6 = schema.node_map(d["nodes"])[w6]
    ck(ok and n6["level"] == 6 and n6["outputs"] == "보고서", "다시 lv6 으로 내리면 값이 되살아남")

    # 6-3. stats 분모 = lv6 만
    s = schema.stats(d)
    lv6_cnt = sum(1 for n in d["nodes"] if n["level"] == 6)
    ck(s["detail_total"] == lv6_cnt, f"detail_total 이 lv6 수와 일치 ({s['detail_total']}=={lv6_cnt})")
    ck(s["ai_yes"] + s["ai_no"] == lv6_cnt, "AI 지표 분모에 상위 레벨이 안 섞임")
    ck(s["total"] > lv6_cnt, f"전체({s['total']})는 lv6({lv6_cnt})보다 많음")
    ck(s["ai_yes"] == 1, f"AI 적용 1건 집계: {s['ai_yes']}")
    ck("시운전1부" in s["by_dept"], "부서 집계에 lv6 값 반영")
    schema.delete_node(d, w4)      # 정리
    d = schema.normalize(d)

    # 7. 손상 데이터 정규화 (고아 / 사이클 / 결측)
    bad = {"nodes": [
        {"id": "a", "parent_id": "없음", "name": "고아"},
        {"id": "b", "parent_id": "c", "name": "순환B", "level": 4},
        {"id": "c", "parent_id": "b", "name": "순환C", "level": 4},
    ]}
    bad = schema.normalize(bad)
    ck(schema.validate(bad) == [], f"손상 데이터 정규화 후 validate 통과: {schema.validate(bad)}")
    ck(schema.node_map(bad["nodes"])["a"]["parent_id"] == schema.ROOT_ID, "고아 노드를 ROOT 로 구제 (삭제 아님)")

    # 7-1. 최초 로드가 시드를 파일로 고정하는지 (세션마다 id 가 달라지면 엑셀 왕복이 깨진다)
    import shutil as _sh
    _fresh = _TMP / "fresh"
    os.environ["PROCESS_DATA_PATH"] = str(_fresh)
    pc.invalidate_cache()
    ck(not pc.tree_path().exists(), "새 설치: 데이터 파일 없음")
    s1, _ = store.load_tree()
    ck(pc.tree_path().exists(), "첫 로드가 시드를 파일로 고정")
    s2, _ = store.load_tree()
    ck([n["id"] for n in s1["nodes"]] == [n["id"] for n in s2["nodes"]],
       "두번째 로드가 같은 id 를 반환 (세션마다 재생성 안 함)")
    xb_f = excel_io.build_xlsx(s1, mask=True)
    parsed_f, _ = excel_io.parse_excel(xb_f, s2)
    df_f = schema.diff(s2, parsed_f)
    ck(not df_f["added"] and not df_f["removed"],
       f"새 설치에서 엑셀 왕복 시 중복 없음 (추가{len(df_f['added'])}/삭제{len(df_f['removed'])})")
    _sh.rmtree(_fresh, ignore_errors=True)
    os.environ["PROCESS_DATA_PATH"] = str(_TMP)
    pc.invalidate_cache()

    # 8. 저장 / 로드 왕복
    d = schema.normalize(d)
    r1 = store.save_tree(d, "김철수")
    ck(r1.ok and r1.rev == 1, f"첫 저장 rev={r1.rev}")
    ck(pc.tree_path().exists(), "정본 파일 생성")
    loaded, warns = store.load_tree()
    ck(warns == [], f"로드 경고 없음: {warns}")
    ck(len(loaded["nodes"]) == len(d["nodes"]), "저장/로드 노드 수 일치")
    ck(loaded["updated_by"] == "김철수", "작성자 기록")

    r_noauthor = store.save_tree(loaded, "  ")
    ck(not r_noauthor.ok and "저장자" in r_noauthor.error, "저장자 미입력 저장 거부")

    # 9. 충돌 검사
    stale = json.loads(json.dumps(loaded))
    stale["rev"] = 0                        # 오래된 사본
    r2 = store.save_tree(loaded, "박영희")   # 디스크 rev=1 -> 2
    ck(r2.ok and r2.rev == 2, f"두번째 저장 rev={r2.rev}")
    r3 = store.save_tree(stale, "이몽룡")
    ck(r3.conflict and r3.disk_rev == 2 and r3.disk_author == "박영희",
       f"오래된 사본 저장 시 충돌 감지 (disk_rev={r3.disk_rev}, by={r3.disk_author})")
    r4 = store.save_tree(stale, "이몽룡", force=True)
    ck(r4.ok and r4.rev == 3, f"force 덮어쓰기 rev={r4.rev}")

    # 10. 스냅샷 / 복원
    hist = store.list_history()
    ck(len(hist) >= 2, f"스냅샷 {len(hist)}개 적립")
    ck(store.load_snapshot("../../evil.json") is None, "스냅샷 경로 조작 차단")
    cur, _ = store.load_tree()
    n_before = len(cur["nodes"])
    schema.add_node(cur, schema.ROOT_ID, 3, "이몽룡", "지울부문")
    store.save_tree(cur, "이몽룡")
    after_add, _ = store.load_tree()
    ck(len(after_add["nodes"]) == n_before + 1, "노드 추가 저장")
    target = store.list_history()[0]["file"]
    res, restored = store.restore(target, "관리자")
    ck(res.ok and restored is not None, f"복원 성공 rev={res.rev}")
    reloaded, _ = store.load_tree()
    ck(len(reloaded["nodes"]) == n_before, f"복원으로 노드 수 되돌림 {len(reloaded['nodes'])}=={n_before}")

    # 11. 감사로그
    aud = store.read_audit()
    ck(len(aud) >= 4, f"감사로그 {len(aud)}건")
    ck(any(a.get("action") == "restore" for a in aud), "복원이 감사로그에 기록")
    ck(any(a.get("action") == "force" for a in aud), "강제 덮어쓰기가 감사로그에 기록")

    # 12. diff
    dd = schema.diff(reloaded, after_add)
    ck(len(dd["added"]) == 1 and len(dd["removed"]) == 0, f"diff 추가 1건 감지: {len(dd['added'])}")

    # 13. 엑셀
    df = excel_io.flatten(reloaded, mask=True)
    ck(len(df) == len(reloaded["nodes"]), f"flatten 행 수 {len(df)}")
    ck(list(df.columns)[:7] == ["lv0", "lv1", "lv2", "lv3", "lv4", "lv5", "lv6"], "엑셀 lv0~lv6 컬럼")
    ck((df["lv0"] == "조선").all() and (df["lv2"] == "시운전").all(), "고정 3단 재부착")
    xb = excel_io.build_xlsx(reloaded, mask=True)
    ck(len(xb) > 5000 and xb[:2] == b"PK", f"xlsx 생성 {len(xb)} bytes")

    # 14. 엑셀 왕복 (다운로드 → 업로드)
    parsed, perrs = excel_io.parse_excel(xb, reloaded)
    ck(perrs == [], f"엑셀 재파싱 오류 없음: {perrs}")
    rt = schema.diff(reloaded, parsed)
    ck(not rt["added"] and not rt["removed"] and not rt["changed"],
       f"엑셀 왕복 무손실 (추가{len(rt['added'])}/삭제{len(rt['removed'])}/변경{len(rt['changed'])})")
    # 14b. 기간단위·횟수 엑셀 왕복 (flatten 기간단위/횟수 컬럼 → parse 재읽기)
    fr, _ = store.load_tree()
    fr3 = [n for n in fr["nodes"] if n["level"] == 3][0]["id"]
    fr4 = schema.add_node(fr, fr3, 4, "t", "왕복4")
    fr5 = schema.add_node(fr, fr4, 5, "t", "왕복5")
    fr6 = schema.add_node(fr, fr5, 6, "t", "왕복6")
    schema.update_node(fr, fr6, {"freq_unit": "주", "freq_count": "3"}, "t")
    dff = excel_io.flatten(fr, mask=False)
    ck("기간단위" in dff.columns and "횟수" in dff.columns, "엑셀에 기간단위·횟수 컬럼 존재")
    fr_rt, _ = excel_io.parse_excel(excel_io.build_xlsx(fr, mask=False), fr)
    got = [n for n in fr_rt["nodes"] if n["id"] == fr6][0]
    ck(got.get("freq_unit") == "주" and str(got.get("freq_count")) == "3", "기간단위·횟수 엑셀 왕복 보존")

    # 15. 엑셀 삭제 옵트인 — app.py _apply_import 의 병합 규칙과 동일 로직
    base, _ = store.load_tree()
    trimmed = {**base, "nodes": [n for n in base["nodes"] if n["level"] == 3][:3]}
    trimmed = schema.normalize(trimmed)
    xb2 = excel_io.build_xlsx(trimmed, mask=True)
    parsed2, _ = excel_io.parse_excel(xb2, base)
    d2 = schema.diff(base, parsed2)
    ck(len(d2["removed"]) > 0, f"엑셀에서 행을 지우면 삭제 대상으로 잡힘 ({len(d2['removed'])}개)")
    # delete_missing=False -> 되살려 병합
    merged = schema.normalize({**parsed2, "nodes": list(parsed2["nodes"]) + [dict(n) for n in d2["removed"]]})
    ck(len(schema.diff(base, merged)["removed"]) == 0, "삭제 옵트인 OFF: 사라진 노드를 되살려 병합")
    ck(len(merged["nodes"]) == len(base["nodes"]), "삭제 옵트인 OFF: 노드 수 보존")
    # delete_missing=True -> 실제 삭제
    ck(len(parsed2["nodes"]) < len(base["nodes"]), "삭제 옵트인 ON: 실제로 줄어듦")

    # 16. 도메인 미등록 값 감지 (app.py 가 이 함수로 안내한다)
    dom_base, _ = store.load_tree()
    lv3id = [n for n in dom_base["nodes"] if n["level"] == 3][0]["id"]
    a4 = schema.add_node(dom_base, lv3id, 4, "t", "A")
    a5 = schema.add_node(dom_base, a4, 5, "t", "B")
    a6 = schema.add_node(dom_base, a5, 6, "t", "C")
    schema.update_node(dom_base, a6, {"dept": "없는부서", "tech": ["없는기술", "LLM"],
                                      "automation_level": "없는수준", "frequency": "없는주기"}, "t")
    dom_base = schema.normalize(dom_base)
    unk = excel_io.unknown_domain_values(dom_base)
    ck(unk.get("dept") == ["없는부서"], f"미등록 부서 감지: {unk.get('dept')}")
    ck(unk.get("tech") == ["없는기술"], f"미등록 기술만 감지(LLM 제외): {unk.get('tech')}")
    ck("automation_level" in unk and "frequency" in unk, "미등록 자동화수준·주기 감지")
    dom_base["domains"]["dept"].append("없는부서")
    ck("dept" not in excel_io.unknown_domain_values(dom_base), "도메인에 추가하면 더는 미등록이 아님")

    # 17. 복원 diff 는 schema.diff 가 정본 (프론트가 하드코딩하면 안 됨)
    cur3, _ = store.load_tree()
    snaps = store.list_history()
    if snaps:
        sp = store.load_snapshot(snaps[0]["file"])
        dd3 = schema.diff(cur3, sp)
        ck(isinstance(dd3["added"], list) and isinstance(dd3["removed"], list),
           f"복원 diff 계산 가능 (되살아남 {len(dd3['added'])}/사라짐 {len(dd3['removed'])})")

    # 18. 작업시간
    ck(schema.annual_hours({"work_hours": "0.5", "annual_count": "52"}) == 26.0, "연간 공수 = 0.5 × 52 = 26")
    ck(all(schema.annual_hours(b) == 0.0 for b in
           [{}, {"work_hours": "", "annual_count": "52"}, {"work_hours": "abc", "annual_count": "52"},
            {"work_hours": "2", "annual_count": None}]), "공수 계산이 빈값·문자·None 에 안 죽음")
    ck(schema.FREQ_ANNUAL["주 1회"] == 52 and schema.FREQ_ANNUAL["일 1회"] == 250,
       "주기→연간횟수 매핑 (일 1회는 근무일 250)")
    ck("호선별" not in schema.FREQ_ANNUAL and "수시" not in schema.FREQ_ANNUAL,
       "호선별·수시는 자동 매핑 없음 (직접 입력)")
    ck(all(f in schema.DETAIL_FIELDS for f in ("work_hours", "annual_count")), "작업시간이 lv6 전용 필드")
    ck(all(f in schema.NODE_DEFAULTS for f in ("work_hours", "annual_count")),
       "작업시간이 NODE_DEFAULTS 에 있음 (없으면 diff 가 변경을 못 잡는다)")
    # 18b. 기간단위 칩 + 횟수 → 연간 횟수·공수 파생
    ck(schema.FREQ_UNITS["주"] == 52 and schema.FREQ_UNITS["일"] == 250, "기간단위→연간 (일=근무일 250)")
    ck(schema.annual_count_of({"freq_unit": "주", "freq_count": "3"}) == 156, "주 3회 = 연 156회")
    ck(schema.annual_hours({"work_hours": "0.5", "freq_unit": "주", "freq_count": "3"}) == 78.0,
       "연간 공수 = 0.5 × (3 × 52) = 78")
    ck(schema.annual_count_of({"annual_count": "40"}) == 40, "freq_unit 없으면 annual_count 폴백")
    ck(schema.annual_count_of({"freq_unit": "주", "freq_count": "", "annual_count": "40"}) == 0,
       "freq_unit 있고 횟수 비면 0 (annual_count 로 안 샌다)")
    ck(all(f in schema.DETAIL_FIELDS for f in ("freq_unit", "freq_count")), "기간단위·횟수가 lv6 전용 필드")
    ck(all(f in schema.NODE_DEFAULTS for f in ("freq_unit", "freq_count")),
       "기간단위·횟수가 NODE_DEFAULTS 에 있음 (없으면 diff·엑셀 누락)")
    # 시간만 바뀐 노드를 diff 가 잡는지 — 못 잡으면 엑셀 미리보기가 "변경 0건" 이라 거짓말한다
    h1, _ = store.load_tree()
    h1 = schema.normalize(h1)
    hid = [n for n in h1["nodes"] if n["level"] == 3][0]["id"]
    hl4 = schema.add_node(h1, hid, 4, "t", "H4"); hl5 = schema.add_node(h1, hl4, 5, "t", "H5")
    hl6 = schema.add_node(h1, hl5, 6, "t", "H6")
    h1 = schema.normalize(h1)
    h2 = schema.normalize(json.loads(json.dumps(h1)))
    schema.update_node(h2, hl6, {"work_hours": "3", "annual_count": "12"}, "t")
    ck(len(schema.diff(h1, h2)["changed"]) == 1, "시간만 바꿔도 diff 가 '변경' 으로 잡음")
    hs = schema.stats(h2)
    ck(hs["total_hours"] == 36.0, f"stats 연간 공수 합 = 3 × 12 = 36 (실제 {hs['total_hours']})")
    # 파이썬이 주기로 횟수를 자동 채우면 안 된다 — 일부러 비운 엑셀이 조용히 52 를 얻는다
    fq = schema.normalize({"nodes": [{"id": "x", "parent_id": schema.ROOT_ID, "level": 3,
                                      "name": "F", "frequency": "주 1회"}]})
    ck(fq["nodes"][0]["annual_count"] == "", "파이썬은 주기로 연간횟수를 자동 채우지 않음")

    # 19. 결정론적 시드 id — 개인 배포판과 메인앱이 같은 부문 id 를 써야 취합된다
    ck([n["id"] for n in schema.bootstrap()["nodes"]] == [n["id"] for n in schema.bootstrap()["nodes"]],
       "bootstrap 이 매번 같은 lv3 id 를 만듦")
    ck([n["id"] for n in schema.bootstrap()["nodes"]] == [s[0] for s in schema.SEED_LV3],
       "시드 id 가 SEED_LV3 상수와 일치")

    # 20. 개인 제출 JSON 취합 — 브라우저가 실제로 내보낸 fixture 로 검증
    fx = Path(__file__).resolve().parent / "tests" / "fixtures" / "solo_export_sample.json"
    if fx.exists():
        raw = fx.read_bytes()
        master = schema.bootstrap()
        got, errs = excel_io.parse_json(raw, master)
        ck(errs == [], f"브라우저 제출 fixture 파싱 성공: {errs}")
        d = schema.diff(master, got)
        ck(len(d["added"]) == 3 and len(d["removed"]) == 0,
           f"fixture 반영: 추가 3(lv4/5/6) 삭제 0 (실제 추가 {len(d['added'])} 삭제 {len(d['removed'])})")
        nm = schema.node_map(got["nodes"])
        ck("lv3_seonjang" in nm and nm["lv3_seonjang"]["name"] == "선장운전", "부문이 고정 id 로 매칭됨")
        l6 = [n for n in got["nodes"] if n["level"] == 6][0]
        ck("owner" not in l6 and l6["dept"] == "시운전1부", "lv6 에 소속만 실려 옴 (이름은 저장 안 함)")
        ck(schema.annual_hours(l6) == 15.0, f"제출된 작업시간 반영 (0.5 × 30 = 15, 실제 {schema.annual_hours(l6)})")
        # 같은 파일 재제출 → 멱등
        again, _ = excel_io.parse_json(raw, got)
        ck(len(again["nodes"]) == len(got["nodes"]), "같은 파일 재제출해도 노드가 복제되지 않음(멱등)")
    else:
        ck(False, "fixture 파일 없음: tests/fixtures/solo_export_sample.json")

    # 21. 취합 매칭 규칙
    base2 = schema.bootstrap()
    # (a) lv3 은 이름 경로로 합쳐진다
    pa = schema.bootstrap(); na = schema.add_node(pa, schema.ROOT_ID, 3, "A", "새부문")
    pb = schema.bootstrap(); nb = schema.add_node(pb, schema.ROOT_ID, 3, "B", "새부문")
    m1, _ = excel_io.parse_json(excel_io.build_json_bytes(schema.normalize(pa)), base2)
    m2, _ = excel_io.parse_json(excel_io.build_json_bytes(schema.normalize(pb)), m1)
    ck([n["name"] for n in m2["nodes"]].count("새부문") == 1, "각자 만든 같은 이름의 부문이 하나로 합쳐짐")
    # (b) lv4 는 경로로 합치지 않는다 — 합치면 뒷사람이 앞사람 값을 조용히 덮는다
    lv3a = [n for n in base2["nodes"] if n["level"] == 3][0]["id"]
    qa = schema.bootstrap(); schema.add_node(qa, lv3a, 4, "A", "겹치는대분류")
    qb = schema.bootstrap(); schema.add_node(qb, lv3a, 4, "B", "겹치는대분류")
    k1, _ = excel_io.parse_json(excel_io.build_json_bytes(schema.normalize(qa)), base2)
    k2, _ = excel_io.parse_json(excel_io.build_json_bytes(schema.normalize(qb)), k1)
    merged = schema.normalize({**k2, "nodes": list(k2["nodes"])
                               + [dict(n) for n in schema.diff(k1, k2)["removed"]]})
    ck([n["name"] for n in merged["nodes"]].count("겹치는대분류") == 2,
       "lv4 는 경로로 안 합침 — 둘 다 남아 관리자가 판단")
    # (c) 개인이 만든 도메인은 마스터에 바로 안 섞인다
    td = schema.bootstrap()
    t3 = [n for n in td["nodes"] if n["level"] == 3][0]["id"]
    t4 = schema.add_node(td, t3, 4, "A", "T4"); t5 = schema.add_node(td, t4, 5, "A", "T5")
    t6 = schema.add_node(td, t5, 6, "A", "T6")
    td = schema.normalize(td)
    schema.update_node(td, t6, {"tech": ["듣보기술"]}, "A")
    td["domains"]["tech"].append("듣보기술")
    g, _ = excel_io.parse_json(excel_io.build_json_bytes(td), schema.bootstrap())
    ck("듣보기술" not in g["domains"]["tech"], "개인이 만든 기술이 마스터에 바로 안 섞임")
    ck("듣보기술" in excel_io.unknown_domain_values(g).get("tech", []),
       "대신 unknown_domain_values 가 잡아 관리자 승인 대기")

    # 22. 제출 취합 (collect_jsons) — 경로 병합 + 제출 인원수 N
    ck(all(f in schema.NODE_DEFAULTS for f in ("submit_count", "submit_detail")),
       "submit_count·submit_detail 이 NODE_DEFAULTS 에 있음(diff·엑셀 인지)")
    ck(all(f in excel_io.FIELD_COLS.values() for f in ("submit_count", "submit_detail")),
       "submit_count·submit_detail 이 FIELD_COLS 에 있음(엑셀 왕복)")
    ck(not any(f in schema.DETAIL_FIELDS for f in ("submit_count", "submit_detail")),
       "submit_count·submit_detail 은 DETAIL_FIELDS 아님(집계 분모 오염 방지)")

    def _mkfile(dept, author, wh):
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        n5 = schema.add_node(nd, n4, 5, "x", "레이더")
        n6 = schema.add_node(nd, n5, 6, "x", "레이더동작시험")
        schema.update_node(nd, n6, {"dept": dept, "work_hours": wh, "freq_unit": "주",
                                    "freq_count": "3", "automation_level": "부분자동"}, "x")
        payload = {"exported_by": author, "exported_dept": dept,
                   "nodes": schema.normalize(nd)["nodes"], "domains": {}}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    files = [("프로세스_홍길동_시운전1부_20260721.json", _mkfile("시운전1부", "홍길동", "0.5")),
             ("프로세스_김철수_시운전2부_20260721.json", _mkfile("시운전2부", "김철수", "1")),
             ("프로세스_이영희_시운전1부_20260721.json", _mkfile("시운전1부", "이영희", "0.5"))]
    cmaster = schema.bootstrap()
    merged, reports, cerrs = excel_io.collect_jsons(files, cmaster)
    ck(cerrs == [], f"취합 전역 오류 없음: {cerrs}")
    l6s = [n for n in merged["nodes"] if n["level"] == 6]
    ck(len(l6s) == 1, f"같은 경로가 한 노드로 합쳐짐 (실제 lv6 {len(l6s)}개)")
    ck(l6s[0]["submit_count"] == "3", f"제출 인원수 = 3 (부서,이름) distinct (실제 {l6s[0].get('submit_count')!r})")
    ck(l6s[0]["work_hours"] == "1", "첫 제출자(파일명 정렬 첫째=김철수) 상세값 유지")
    sd = l6s[0].get("submit_detail", "")
    ck("시운전1부" in sd and "시운전2부" in sd, "submit_detail 에 부서별 요약 존재")
    ck("홍길동" not in sd and "김철수" not in sd and "이영희" not in sd,
       "submit_detail 에 이름이 없음(개인정보 최소수집)")
    ck(sum(1 for r in reports if not r["errors"]) == 3, "파일별 리포트 3건 정상")
    # 재취합 멱등 — 같은 파일 다시 넣어도 인원수 불변, 노드 안 늘어남
    merged2, _, _ = excel_io.collect_jsons(files, merged)
    l6s2 = [n for n in merged2["nodes"] if n["level"] == 6]
    ck(len(l6s2) == 1 and l6s2[0]["submit_count"] == "3", "재취합 멱등 — 인원수·노드수 불변")
    # 취합 산출물 엑셀 왕복 보존
    rt, _ = excel_io.parse_excel(excel_io.build_xlsx(merged, mask=False), merged)
    rl6 = [n for n in rt["nodes"] if n["level"] == 6][0]
    ck(rl6.get("submit_count") == "3" and "시운전1부" in rl6.get("submit_detail", ""),
       "제출인원·취합상세 엑셀 왕복 보존")

    # 23. 부서/과 2단 (과만 저장, 부서는 매핑)
    ck(len(schema.DEPT_TREE) == 8, f"DEPT_TREE 부서 8개 (실제 {len(schema.DEPT_TREE)})")
    ck(schema.dept_parent("친환경실증랩") == "친환경실증랩", "친환경실증랩 단독 부서(동명 과) 매핑")
    ck("친환경실증랩" in schema.DEFAULT_DOMAINS["dept"], "친환경실증랩 과가 dept 도메인에 자동 포함")
    ck(schema.dept_parent("선장운전1과") == "시운전1부", "dept_parent 과→부서 매핑")
    ck(schema.dept_parent("운영") == "시운전3부", "dept_parent 운영→시운전3부")
    ck(schema.dept_parent("없는과") == "미분류", "매핑에 없는 과는 미분류")
    ck("선장운전1과" in schema.DEFAULT_DOMAINS["dept"] and "시운전1부" not in schema.DEFAULT_DOMAINS["dept"],
       "DEFAULT_DOMAINS.dept 는 과 리스트(부서 아님)")
    mig = schema.normalize({"domains": {"dept": ["시운전1부", "시운전2부", "시운전3부",
                                                 "기획운영부", "해운부", "해양사업부"]}})
    ck("선장운전1과" in mig["domains"]["dept"] and "시운전1부" not in mig["domains"]["dept"],
       "구 기본 부서리스트 → 과리스트 마이그레이션")
    keep = schema.normalize({"domains": {"dept": ["우리과", "너네과"]}})
    ck(keep["domains"]["dept"] == ["우리과", "너네과"], "사용자 편집 dept 는 마이그레이션 안 함(보존)")

    # 23-b. 소속 정규화 — 옛 부서명·오타·대소문자 흡수 (미분류로 새지 않게)
    ck(schema.dept_parent("시운전1부") == "시운전1부", "부서명 그대로 적힌 옛 값도 부서로 인식")
    ck(schema.dept_parent("기획운영부") == "기획운영", "옛 부서명 별칭 → 현 부서")
    # 해양사업부는 부서 목록에서 제거했다 — 되살아나면 선택 목록에 과처럼 한 줄 생긴다
    ck(schema.dept_parent("해양사업부") == "미분류", "해양사업부는 부서가 아니다(제거됨)")
    ck("해양사업부" not in schema.DEFAULT_DOMAINS["dept"], "해양사업부가 dept 도메인에 없다")
    ck(schema.dept_parent("cedar  csu") == "시운전3부", "대소문자·공백 흔들림 흡수")
    ck(schema.canon_dept("cedar csu") == "CEDAR CSU", "canon_dept 과 정식표기로 교정")
    ck(schema.canon_dept("듣보과") == "듣보과", "매핑에 없는 값은 원문 보존(유실 금지)")
    ck(schema.canon_dept("") == "", "빈 소속은 빈 문자열")
    nd_dp = schema.bootstrap()
    _n4 = schema.add_node(nd_dp, "lv3_seonjang", 4, "x", "A")
    _n5 = schema.add_node(nd_dp, _n4, 5, "x", "B")
    _n6 = schema.add_node(nd_dp, _n5, 6, "x", "C")
    schema.update_node(nd_dp, _n6, {"dept": "cedar csu"}, "x")
    nd_dp = schema.normalize(nd_dp)
    ck(schema.node_map(nd_dp["nodes"])[_n6]["dept"] == "CEDAR CSU", "normalize 가 노드 dept 를 정식표기로")

    # 23-c. lv3 부문 이름 오타 교정 (CUS → CSU). 취합이 이름 경로로 병합하므로 갈리면 안 된다.
    nd_l3 = schema.bootstrap()
    bad3 = schema.add_node(nd_l3, schema.ROOT_ID, 3, "x", "Cedar CUS")
    bad3b = schema.add_node(nd_l3, schema.ROOT_ID, 3, "x", "eni  cus")
    deep = schema.add_node(nd_l3, bad3, 4, "x", "ENI CUS")     # lv4 는 건드리지 않는다
    nd_l3 = schema.normalize(nd_l3)
    m3 = schema.node_map(nd_l3["nodes"])
    ck(m3[bad3]["name"] == "CEDAR CSU", "lv3 CUS 오타 → CEDAR CSU")
    ck(m3[bad3b]["name"] == "ENI CSU", "lv3 오타 교정은 대소문자·공백 무시")
    ck(m3[deep]["name"] == "ENI CUS", "lv3 아닌 레벨의 이름은 건드리지 않는다")
    ck(schema.canon_lv3_name("선장운전") == "선장운전", "교정 대상 아닌 부문명은 원문 그대로")
    # 오타뿐 아니라 **대소문자만 다른 정식 이름**도 통일해야 취합에서 부문이 갈리지 않는다
    for v in ("Cedar CSU", "cedar csu", "CEDAR  CSU"):
        ck(schema.canon_lv3_name(v) == "CEDAR CSU", f"lv3 '{v}' → CEDAR CSU (대소문자 통일)")
    ck(schema.canon_lv3_name("Eni Csu") == "ENI CSU", "lv3 ENI CSU 대소문자 통일")
    ck(schema.canon_lv3_name("zlng csu") == "ZLNG CSU", "오타맵에 없는 과 이름도 정식표기로")
    for v in ("해운부", "기획운영", "코멘더"):
        ck(schema.canon_lv3_name(v) == v, f"과/부서와 동명인 시드 부문 '{v}' 는 그대로")
    ck(schema.canon_lv3_name("듣보부문") == "듣보부문", "매핑에 없는 부문명은 원문 보존")
    nd_c = schema.bootstrap()
    _bad = schema.add_node(nd_c, schema.ROOT_ID, 3, "x", "Cedar CSU")
    nd_c = schema.normalize(nd_c)
    ck(schema.node_map(nd_c["nodes"])[_bad]["name"] == "CEDAR CSU", "normalize 가 lv3 대소문자도 통일")

    # 23-d. 집계 현재/향후 분리 (요약·드릴다운의 근거)
    nd_st = schema.bootstrap()
    _s4 = schema.add_node(nd_st, "lv3_seonjang", 4, "x", "S4")
    _s5 = schema.add_node(nd_st, _s4, 5, "x", "S5")
    _a6 = schema.add_node(nd_st, _s5, 6, "x", "A6")
    _b6 = schema.add_node(nd_st, _s5, 6, "x", "B6")
    _c6 = schema.add_node(nd_st, _s5, 6, "x", "C6")
    _a7 = schema.add_node(nd_st, _a6, 7, "x", "A7")
    schema.update_node(nd_st, _a6, {"tech": ["LLM"], "dept": "선장운전1과"}, "x")
    schema.update_node(nd_st, _a7, {"tech": ["LLM", "OCR"], "future_tech": ["RPA"]}, "x")   # lv7 → lv6 롤업
    schema.update_node(nd_st, _b6, {"future_tech": ["RPA"], "dept": "선장운전1과"}, "x")
    schema.update_node(nd_st, _c6, {"dept": "시운전1부"}, "x")        # 옛 부서명이 그대로 들어간 노드
    stt = schema.stats(schema.normalize(nd_st))
    ck(stt["detail_total"] == 3, f"분모는 lv6 만 (실제 {stt['detail_total']})")
    ck(stt["ai_yes"] == 1 and stt["ai_future_yes"] == 2,
       f"현재/향후 AI 를 따로 센다 (현재 {stt['ai_yes']} 향후 {stt['ai_future_yes']})")
    ck(stt["ai_rate"] == 33 and stt["ai_future_rate"] == 67, "적용률은 파이썬이 계산한다(현재·향후 각각)")
    ck(stt["by_tech_now"] == {"LLM": 1, "OCR": 1}, f"현재기술은 lv7 자식까지 롤업 (실제 {stt['by_tech_now']})")
    ck(stt["by_tech_future"] == {"RPA": 2}, f"향후기술 집계 분리 (실제 {stt['by_tech_future']})")
    # 과 2개(선장운전1과) + 부서명 그대로인 1개가 모두 '시운전1부' 로 모인다
    ck(stt["by_dept_group"].get("시운전1부") == 3,
       f"옛 부서명 노드도 부서 롤업에 잡힌다(미분류 아님) — {stt['by_dept_group']}")
    ck("미분류" not in stt["by_dept_group"], "매핑되는 값은 미분류로 새지 않는다")
    ck(stt["by_dept"].get("시운전1부") == 1 and stt["by_dept"].get("선장운전1과") == 2,
       f"과별 표에는 저장된 원문 그대로 (실제 {stt['by_dept']})")
    ck("by_owner" not in stt, "담당자 축은 제거됐다")

    # 23-f. 담당자(owner) 완전 제거 — 옛 저장본의 이름은 로드 시점에 사라진다
    ck("owner" not in schema.NODE_DEFAULTS and "owner" not in schema.DETAIL_FIELDS,
       "owner 가 스키마에서 빠졌다")
    old_own = schema.normalize({"nodes": [{"id": "o1", "parent_id": "__root__", "level": 6,
                                           "name": "옛업무", "owner": "홍길동", "dept": "선장운전1과"}]})
    ck("owner" not in old_own["nodes"][0], "normalize 가 옛 owner 값을 지운다")
    ck(old_own["nodes"][0]["dept"] == "선장운전1과", "소속은 그대로 남는다")
    ck("담당자" not in excel_io.TREE_COLS, "엑셀 계층도 시트에 담당자 열이 없다")
    ck("저장자" in excel_io.TREE_COLS, "저장자(updated_by)는 별개라 남는다 — 취합본을 다듬는 사람")

    # 23-g. 소속 다중 귀속 — 한 업무를 여러 과가 수행
    nd_md = schema.bootstrap()
    _p4 = schema.add_node(nd_md, "lv3_seonjang", 4, "x", "P4")
    _p5 = schema.add_node(nd_md, _p4, 5, "x", "P5")
    _p6 = schema.add_node(nd_md, _p5, 6, "x", "공동업무")
    schema.update_node(nd_md, _p6, {"depts": ["선장운전1과", "선장운전2과", "전장운전1과"],
                                    "tech": ["LLM"], "work_hours": "1", "annual_count": "10"}, "x")
    nd_md = schema.normalize(nd_md)
    s_md = schema.stats(nd_md)
    # ★ KPI(팀 단위)와 과별(수행 주체)은 **단위가 다르다** — 합이 안 맞는 게 정상이다
    ck(s_md["detail_total"] == 1, f"KPI 세부업무 수는 팀 단위로 1 (실제 {s_md['detail_total']})")
    ck(sum(s_md["by_dept"].values()) == 3, f"과별 합계는 3 (실제 {s_md['by_dept']})")
    ck(all(s_md["by_dept"][k] == 1 for k in ("선장운전1과", "선장운전2과", "전장운전1과")),
       "제출한 과 모두에 1씩")
    # 부서 롤업은 **부서 집합으로 dedupe** — 같은 부서의 두 과가 해도 그 부서는 1회
    ck(s_md["by_dept_group"].get("시운전1부") == 1,
       f"같은 부서의 두 과는 부서 롤업에서 1회 (실제 {s_md['by_dept_group']})")
    ck(s_md["by_dept_group"].get("시운전2부") == 1, "다른 부서는 따로 잡힌다")
    ck(sum(s_md["by_dept_ai_now"].values()) == 3, "과별 AI 도 수행 과 모두에")
    for k in s_md["by_dept_ai_now"]:
        ck(s_md["by_dept_ai_now"][k] <= s_md["by_dept"][k], f"[{k}] 과별 AI ≤ 과별 업무 수 (그래프 공통 눈금 전제)")
    # 양방향 백필
    bf = schema.normalize({"nodes": [{"id": "b1", "parent_id": "__root__", "level": 6,
                                      "name": "옛", "dept": "선장운전1과"}]})["nodes"][0]
    ck(bf["depts"] == ["선장운전1과"], "dept 만 있던 옛 데이터 → depts 백필")
    bf2 = schema.normalize({"nodes": [{"id": "b2", "parent_id": "__root__", "level": 6,
                                       "name": "새", "depts": ["ZLNG CSU"]}]})["nodes"][0]
    ck(bf2["dept"] == "ZLNG CSU", "depts 만 있으면 대표 과를 dept 로 백필")
    dedup = schema.normalize({"nodes": [{"id": "b3", "parent_id": "__root__", "level": 6, "name": "d",
                                         "depts": ["선장운전1과", "선장운전1과", "cedar csu"]}]})["nodes"][0]
    ck(dedup["depts"] == ["선장운전1과", "CEDAR CSU"], f"depts 정규화+중복제거 (실제 {dedup['depts']})")

    # 23-h. 부하 미입력 vs 호선루틴 보류 — 성격이 달라 따로 센다
    nd_ms2 = schema.bootstrap()
    _q4 = schema.add_node(nd_ms2, "lv3_seonjang", 4, "x", "Q4")
    _q5 = schema.add_node(nd_ms2, _q4, 5, "x", "Q5")
    _blank = schema.add_node(nd_ms2, _q5, 6, "x", "부하없음")            # 아무것도 안 씀
    _ship = schema.add_node(nd_ms2, _q5, 6, "x", "호선루틴업무")
    _nodept = schema.add_node(nd_ms2, _q5, 6, "x", "소속없지만부하있음")
    schema.update_node(nd_ms2, _ship, {"occur_pattern": "호선루틴", "work_hours": "2"}, "x")
    schema.update_node(nd_ms2, _nodept, {"work_hours": "2", "annual_count": "5"}, "x")
    s_ms = schema.stats(schema.normalize(nd_ms2))
    ck(s_ms["missing_total"] == 1, f"부하 미입력은 1건 (실제 {s_ms['missing_total']})")
    ck(s_ms["unresolved_total"] == 1, f"호선루틴 보류는 따로 1건 (실제 {s_ms['unresolved_total']})")
    ck(schema.is_ship_routine(schema.node_map(nd_ms2["nodes"])[_ship]), "호선루틴 판정")
    # ★ 소속이 비어도 **부하가 있으면 미입력이 아니다** (옛 정의였다면 잡혔다)
    ck(s_ms["by_dept"].get("(미지정)") == 3 and s_ms["missing_total"] == 1,
       "소속 유무는 부하 미입력과 무관하다")

    # 23-j. 향후 AI 적용 시기 — 기술별 연도 + 롤업 최댓값
    ck("future_years" in schema.NODE_DEFAULTS and "future_years" in schema.DETAIL_FIELDS,
       "future_years 가 NODE_DEFAULTS∧DETAIL_FIELDS (없으면 취합이 복사하지 않아 유실)")
    ck(schema.FUTURE_YEARS == ("2027", "2028", "2029", "2030", "2031"), "적용 시기 선택지 2027~2031")
    nd_fy = schema.bootstrap()
    _f4 = schema.add_node(nd_fy, "lv3_seonjang", 4, "x", "F4")
    _f5 = schema.add_node(nd_fy, _f4, 5, "x", "F5")
    _f6 = schema.add_node(nd_fy, _f5, 6, "x", "세부")
    _f7 = schema.add_node(nd_fy, _f6, 7, "x", "단위")
    schema.update_node(nd_fy, _f6, {"future_tech": ["RPA", "LLM"],
                                    "future_years": {"RPA": "2027", "LLM": "2029",
                                                     "OCR": "2028", "X": "1999"}}, "x")
    schema.update_node(nd_fy, _f7, {"future_tech": ["RPA", "OCR"],
                                    "future_years": {"RPA": "2031", "OCR": "2028"}}, "x")
    nd_fy = schema.normalize(nd_fy)
    mfy, cfy = schema.node_map(nd_fy["nodes"]), schema.children_index(nd_fy["nodes"])
    ck(mfy[_f6]["future_years"] == {"RPA": "2027", "LLM": "2029"},
       f"선택 안 한 기술(OCR)·범위 밖 연도(1999)는 버린다 (실제 {mfy[_f6]['future_years']})")
    roll = schema.rollup_future_years(cfy, mfy[_f6])
    ck(roll == {"RPA": "2031", "LLM": "2029", "OCR": "2028"},
       f"lv7 자식까지 병합 (실제 {roll})")
    ck(roll["RPA"] == "2031", "같은 기술이 갈리면 **늦은 해**가 이긴다(완료 시점 기준)")
    ck(schema.future_year_max(cfy, mfy[_f6]) == "2031", "최댓값 = 완료 시점")
    ck(schema.future_year_max(cfy, mfy[_f5]) == "", "적용 시기가 없으면 빈 문자열")
    # 엑셀 — 파생 2열로만 나가고 **역수입되지 않는다**(객체 맵, JSON 이 정본)
    ck("향후적용시기" in excel_io.DERIVED_COLS and "향후완료시점" in excel_io.DERIVED_COLS,
       "향후적용시기·향후완료시점은 파생열(DERIVED_COLS)")
    ck("향후적용시기" not in excel_io.FIELD_COLS and "향후완료시점" not in excel_io.FIELD_COLS,
       "파생열은 FIELD_COLS 에 없다 — 있으면 parse_excel 이 역수입한다")
    _fdf = excel_io.flatten(nd_fy)
    _r6 = _fdf[_fdf["id"] == _f6].iloc[0]
    ck(_r6["향후적용시기"] == "LLM:2029, RPA:2027", f"엑셀 적용시기 조인 (실제 {_r6['향후적용시기']})")
    ck(_r6["향후완료시점"] == "2029", f"엑셀 완료시점은 자기 값 기준 (실제 {_r6['향후완료시점']})")

    # 23-i. 발생패턴이 취합에서 유실되지 않는다 (부하 엔진의 입력)
    for f in ("occur_pattern", "apply_phases", "events"):
        ck(f in schema.NODE_DEFAULTS and f in schema.DETAIL_FIELDS,
           f"{f} 가 NODE_DEFAULTS∧DETAIL_FIELDS (없으면 취합이 복사하지 않아 유실)")
    ev = schema.normalize({"nodes": [{"id": "e1", "parent_id": "__root__", "level": 6, "name": "e",
                                      "events": [{"event": "GT", "offset_start": "before", "offset_days": "7"},
                                                 {"event": "", "offset_days": "3"}]}]})["nodes"][0]
    ck(len(ev["events"]) == 1 and ev["events"][0]["event"] == "GT", "events 정규화 — 빈 줄 제거")
    # 기술은 다중선택이라 합계가 lv6 수보다 클 수 있다 — 중복이 아니라 정상
    ck(sum(stt["by_tech_now"].values()) >= stt["ai_yes"], "다중선택 축 합계 ≥ 업무 수")
    # 요약 시트에도 두 축이 다 나오는지 (엑셀·화면 축 일치)
    sdf = excel_io._summary_df(schema.normalize(nd_st))
    gubun = set(sdf["구분"])
    for want in ("AI 에이전트 — 현재", "AI 에이전트 — 향후", "활용기술 — 현재", "활용기술 — 향후", "부서별"):
        ck(any(str(g).startswith(want) for g in gubun), f"요약 시트에 '{want}' 축이 있다")
    ck(not any(str(g).startswith("담당자별") for g in gubun), "요약 시트에 담당자별 축이 없다")

    # 23-e. 요약 화면용 축 — 과별 AI(현재/향후) + 미입력
    ck(stt["by_dept_ai_now"].get("선장운전1과") == 1, f"과별 AI 현재 (실제 {stt['by_dept_ai_now']})")
    ck(stt["by_dept_ai_future"].get("선장운전1과") == 2, f"과별 AI 향후 (실제 {stt['by_dept_ai_future']})")
    for m in ("by_dept_ai_now", "by_dept_ai_future"):
        ck(all(stt[m][k] <= stt["by_dept"].get(k, 0) for k in stt[m]),
           f"{m} 은 과별 업무 수를 넘지 않는다(그래프 공통 눈금의 전제)")
    ck(sum(stt["by_dept_ai_now"].values()) == stt["ai_yes"], "과별 AI 현재 합 = 전체 AI 현재")
    ck(sum(stt["by_dept_ai_future"].values()) == stt["ai_future_yes"], "과별 AI 향후 합 = 전체 AI 향후")
    # 미입력 = 소속이 비었거나 롤업 공수 0. 위 픽스처는 work_hours 를 아무도 안 넣었으므로 전부 미입력.
    ck(stt["missing_total"] == stt["detail_total"], f"미입력 집계 (실제 {stt['missing_total']}/{stt['detail_total']})")
    nd_ms = schema.bootstrap()
    _m4 = schema.add_node(nd_ms, "lv3_seonjang", 4, "x", "M4")
    _m5 = schema.add_node(nd_ms, _m4, 5, "x", "M5")
    _ok6 = schema.add_node(nd_ms, _m5, 6, "x", "채운업무")
    schema.update_node(nd_ms, _ok6, {"dept": "선장운전1과", "work_hours": "2", "annual_count": "10"}, "x")
    ck(schema.stats(schema.normalize(nd_ms))["missing_total"] == 0, "소속·부하가 다 있으면 미입력 0")

    # 24. 취합 인원수 = lv6 실제 작성 기준 (공유 골격 lv4/lv5 부풀림 없음)
    def _mkf(gwa, author, l6name):
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        n5 = schema.add_node(nd, n4, 5, "x", "레이더")
        n6 = schema.add_node(nd, n5, 6, "x", l6name)
        schema.update_node(nd, n6, {"dept": gwa, "work_hours": "1"}, "x")
        return json.dumps({"exported_by": author, "exported_dept": gwa,
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}}, ensure_ascii=False).encode("utf-8")
    fs = [("프로세스_A_선장운전1과_20260721.json", _mkf("선장운전1과", "A", "레이더동작시험")),
          ("프로세스_B_선장운전2과_20260721.json", _mkf("선장운전2과", "B", "레이더동작시험")),
          ("프로세스_C_전장운전1과_20260721.json", _mkf("전장운전1과", "C", "자이로시험"))]
    mg, _, _ = excel_io.collect_jsons(fs, schema.bootstrap())
    byname = {n["name"]: n for n in mg["nodes"] if n["level"] >= 4}
    ck(not byname["항해장비"].get("submit_count"), "lv4 공유 골격엔 인원수 없음(부풀림 방지)")
    ck(not byname["레이더"].get("submit_count"), "lv5 공유 골격엔 인원수 없음")
    ck(byname["레이더동작시험"].get("submit_count") == "2", "실제 2명이 한 lv6 만 '2명'")
    ck(not byname["자이로시험"].get("submit_count"), "1명만 한 lv6 는 배지 없음(N<2)")
    ck(schema.dept_parent(byname["레이더동작시험"]["submit_detail"].split(" · ")[0]) in ("시운전1부", "시운전2부"),
       "submit_detail 은 과 기준(부서로 롤업 가능)")
    # ★ 취합이 **제출한 과를 모두** 기록한다 — 예전엔 첫 제출자 과만 남아 나머지가 집계에서 사라졌다
    ck(sorted(byname["레이더동작시험"]["depts"]) == ["선장운전1과", "선장운전2과"],
       f"2개 과가 낸 업무는 depts 에 둘 다 (실제 {byname['레이더동작시험']['depts']})")
    ck(byname["자이로시험"]["depts"] == ["전장운전1과"], "1개 과가 낸 업무는 그 과만")
    s_mg = schema.stats(mg)
    ck(s_mg["by_dept"].get("선장운전1과") == 1 and s_mg["by_dept"].get("선장운전2과") == 1,
       f"취합 후 과별 집계가 두 과 모두에 (실제 {s_mg['by_dept']})")
    ck(s_mg["detail_total"] == 2, f"KPI 는 팀 단위 2건 (실제 {s_mg['detail_total']})")
    ck(sum(s_mg["by_dept"].values()) == 3, "과별 합계 3 ≠ KPI 2 — 단위가 다르다")

    # 24-d. **재취합이 소속을 정정한다** — 누적이면 옛 과가 영영 안 사라져 정정이 불가능하다
    def _mk_one(gwa, author, l6name):
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        n5 = schema.add_node(nd, n4, 5, "x", "레이더")
        n6 = schema.add_node(nd, n5, 6, "x", l6name)
        schema.update_node(nd, n6, {"dept": gwa, "depts": [gwa], "work_hours": "1"}, "x")
        return json.dumps({"exported_by": author, "exported_dept": gwa,
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}}, ensure_ascii=False).encode("utf-8")
    # ① 먼저 '시운전과' 로 취합된 상태를 만든다
    m1, _, _ = excel_io.collect_jsons([("p_A_시운전과.json", _mk_one("시운전과", "A", "정정대상"))], schema.bootstrap())
    t1 = [n for n in m1["nodes"] if n["name"] == "정정대상"][0]
    ck(t1["depts"] == ["시운전과"], f"1회차: 시운전과 (실제 {t1['depts']})")
    ck(schema.stats(m1)["by_dept_group"].get("안벽의장") == 1, "시운전과는 안벽의장으로 롤업된다")
    # ② 제출본의 소속을 고쳐 **다시 취합** → 옛 과가 사라져야 한다
    m2, _, _ = excel_io.collect_jsons([("p_A_안벽의장1과.json", _mk_one("안벽의장1과", "A", "정정대상"))], m1)
    t2 = [n for n in m2["nodes"] if n["name"] == "정정대상"][0]
    ck(t2["depts"] == ["안벽의장1과"], f"재취합: 소속이 **교체**된다 (실제 {t2['depts']})")
    ck("시운전과" not in t2["depts"], "옛 소속이 남지 않는다 (누적이면 정정 불가)")
    ck(t2["dept"] == "안벽의장1과", "대표 과도 함께 갱신")
    s2 = schema.stats(m2)
    ck(s2["by_dept"].get("시운전과") is None, f"과별 집계에서 옛 과가 사라진다 (실제 {s2['by_dept']})")
    ck(sum(s2["by_dept"].values()) == 1, "중복 계상되지 않는다 (누적이면 2가 된다)")
    # ③ 같은 회차에 두 과가 내면 다중 귀속은 그대로 유지
    m3, _, _ = excel_io.collect_jsons(
        [("p_A_안벽의장1과.json", _mk_one("안벽의장1과", "A", "공동작업")),
         ("p_B_안벽의장2과.json", _mk_one("안벽의장2과", "B", "공동작업"))], schema.bootstrap())
    t3 = [n for n in m3["nodes"] if n["name"] == "공동작업"][0]
    ck(sorted(t3["depts"]) == ["안벽의장1과", "안벽의장2과"], f"같은 회차 다중 제출은 유지 (실제 {t3['depts']})")
    # ④ 이번 스캔에 없는 경로는 건드리지 않는다
    m4, _, _ = excel_io.collect_jsons([("p_A_안벽의장1과.json", _mk_one("안벽의장1과", "A", "다른업무"))], m1)
    keep = [n for n in m4["nodes"] if n["name"] == "정정대상"][0]
    ck(keep["depts"] == ["시운전과"], "이번 스캔에 없는 경로의 소속은 그대로 둔다")

    # 24-b. 취합은 **lv6 에 닿는 가지만** 병합한다 (골격만 낸 제출은 통째로 제외)
    def _mk_skel():
        """lv5 까지만 만든 제출 — 세부업무가 하나도 없다."""
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_gijang", 4, "x", "골격대분류")
        schema.add_node(nd, n4, 5, "x", "골격중분류")
        return json.dumps({"exported_by": "Z", "exported_dept": "기장운전1과",
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}}, ensure_ascii=False).encode("utf-8")
    base_n = len(schema.bootstrap()["nodes"])
    mg2, rep2, _ = excel_io.collect_jsons([("프로세스_Z_기장운전1과_20260721.json", _mk_skel())], schema.bootstrap())
    ck(len(mg2["nodes"]) == base_n, "lv6 없는 골격 제출은 노드를 추가하지 않는다")
    ck("골격대분류" not in {n["name"] for n in mg2["nodes"]}, "골격 lv4 가 정본 트리에 남지 않는다")
    ck("lv6" in rep2[0]["errors"] or "세부업무" in rep2[0]["errors"], "제외 사유를 리포트에 남긴다(조용한 0건 금지)")
    # lv6 가 있는 가지는 조상까지 살아남는다 + 형제 골격 가지만 제외
    def _mk_mixed():
        nd = schema.bootstrap()
        good4 = schema.add_node(nd, "lv3_gijang", 4, "x", "쓰는대분류")
        good5 = schema.add_node(nd, good4, 5, "x", "쓰는중분류")
        schema.add_node(nd, good5, 6, "x", "진짜세부업무")
        dead4 = schema.add_node(nd, "lv3_gijang", 4, "x", "빈대분류")
        schema.add_node(nd, dead4, 5, "x", "빈중분류")
        return json.dumps({"exported_by": "Y", "exported_dept": "기장운전2과",
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}}, ensure_ascii=False).encode("utf-8")
    mg3, rep3, _ = excel_io.collect_jsons([("프로세스_Y_기장운전2과_20260721.json", _mk_mixed())], schema.bootstrap())
    names3 = {n["name"] for n in mg3["nodes"]}
    ck({"쓰는대분류", "쓰는중분류", "진짜세부업무"} <= names3, "lv6 에 닿는 가지는 조상까지 병합된다")
    ck("빈대분류" not in names3 and "빈중분류" not in names3, "같은 파일 안의 빈 가지만 골라 제외한다")
    ck(rep3[0]["skipped"] == 2, f"제외 노드 수를 리포트에 담는다 (실제 {rep3[0]['skipped']})")

    # 24-c. 빈 가지 정리 — 정본 트리에 이미 있는 lv6 미도달 가지를 제거
    nd_pr = schema.bootstrap()                      # 시드 lv3 10개 (전부 비어 있다)
    k4 = schema.add_node(nd_pr, "lv3_gijang", 4, "x", "살릴대분류")
    k5 = schema.add_node(nd_pr, k4, 5, "x", "살릴중분류")
    k6 = schema.add_node(nd_pr, k5, 6, "x", "살릴세부업무")
    k7 = schema.add_node(nd_pr, k6, 7, "x", "살릴단위작업")
    e4 = schema.add_node(nd_pr, "lv3_gijang", 4, "x", "빈대분류")
    schema.add_node(nd_pr, e4, 5, "x", "빈중분류")
    nd_pr = schema.normalize(nd_pr)
    before = len(nd_pr["nodes"])
    pruned, removed = excel_io.prune_empty_branches(nd_pr)
    kept = {n["name"] for n in pruned["nodes"]}
    ck({"살릴대분류", "살릴중분류", "살릴세부업무", "살릴단위작업"} <= kept, "lv6 에 닿는 가지는 조상·lv7 까지 남는다")
    ck("빈대분류" not in kept and "빈중분류" not in kept, "빈 가지는 제거된다")
    ck("기장운전" in kept, "lv6 를 품은 lv3 부문은 남는다")
    ck("선장운전" not in kept, "아무것도 없는 시드 부문도 제거된다(정본 트리 전체 정리)")
    ck(len(removed) == before - len(pruned["nodes"]), "제거 목록 수 = 실제 줄어든 노드 수")
    ck(len(schema.validate(pruned)) == 0, f"정리 후에도 구조가 정상: {schema.validate(pruned)}")
    # 지울 게 없으면 원본을 그대로 돌려준다(불필요한 normalize 왕복 없음)
    again, removed2 = excel_io.prune_empty_branches(pruned)
    ck(removed2 == [] and len(again["nodes"]) == len(pruned["nodes"]), "두 번 정리해도 더 지워지지 않는다(멱등)")

    # 25. lv6 상세 개편 — 신규 도메인·필드·파생·마이그레이션·왕복
    ck("ship_type" in schema.DEFAULT_DOMAINS and "special_note" in schema.DEFAULT_DOMAINS,
       "ship_type·special_note 도메인 존재")
    ck(schema.DEFAULT_DOMAINS["ship_type"] == ["CNT", "COT", "LNG", "SHTL", "VLAC", "VLCC", "FLNG"],
       "적용선종 도메인 값")
    for f in ("future_tech", "has_ai_future", "special_note", "ship_types", "linked_systems"):
        ck(f in schema.NODE_DEFAULTS and f in schema.DETAIL_FIELDS, f"{f} 가 NODE_DEFAULTS∧DETAIL_FIELDS")
    # 파생 AI: 현재=tech, 향후=future_tech
    dn = schema.normalize({"nodes": [{"id": "a", "parent_id": "__root__", "level": 6, "name": "a",
                                      "tech": ["LLM"], "future_tech": []}]})["nodes"][0]
    ck(dn["has_ai_agent"] is True and dn["has_ai_future"] is False, "현재 AI=활용기술, 향후 AI=향후기술 파생")
    dn2 = schema.normalize({"nodes": [{"id": "b", "parent_id": "__root__", "level": 6, "name": "b",
                                       "tech": [], "future_tech": ["OCR"], "has_ai_agent": True}]})["nodes"][0]
    ck(dn2["has_ai_agent"] is False and dn2["has_ai_future"] is True, "tech 비면 현재 AI False(수동값 무시)")
    # linked_systems 마이그레이션(구 단일 → 신 다건)
    mig = schema.normalize({"nodes": [{"id": "c", "parent_id": "__root__", "level": 6, "name": "c",
                                       "linked_system": "SAP", "linked_system_detail": "MM"}]})["nodes"][0]
    ck(mig["linked_systems"] == [{"system": "SAP", "detail": "MM"}], "구 연계 단일 → 다건 마이그레이션")
    # 엑셀 왕복(future_tech·special_note·ship_types)
    e = schema.bootstrap()
    e4 = schema.add_node(e, "lv3_seonjang", 4, "x", "A"); e5 = schema.add_node(e, e4, 5, "x", "B")
    e6 = schema.add_node(e, e5, 6, "x", "C")
    schema.update_node(e, e6, {"tech": ["LLM"], "future_tech": ["OCR", "RPA"],
                               "special_note": ["SG", "LPG"], "ship_types": ["CNT", "LNG"]}, "x")
    e = schema.normalize(e)
    ert, _ = excel_io.parse_excel(excel_io.build_xlsx(e, mask=False), e)
    r6 = [n for n in ert["nodes"] if n["level"] == 6][0]
    ck(r6["future_tech"] == ["OCR", "RPA"] and r6["special_note"] == ["SG", "LPG"]
       and r6["ship_types"] == ["CNT", "LNG"], "향후기술·특이사항·선종 엑셀 왕복")
    ck(all(c in excel_io.FIELD_COLS.values() for c in ("future_tech", "special_note", "ship_types")),
       "신규 다중필드가 FIELD_COLS")
    # unknown_domain_values 가 신규 도메인 미등록값 감지
    ud = excel_io.unknown_domain_values(schema.normalize({"domains": schema.DEFAULT_DOMAINS,
        "nodes": [{"id": "u", "parent_id": "__root__", "level": 6, "name": "u",
                   "ship_types": ["없선"], "special_note": ["없특"], "future_tech": ["없기"]}]}))
    ck("없선" in ud.get("ship_type", []) and "없특" in ud.get("special_note", [])
       and "없기" in ud.get("tech", []), "미등록 선종·특이사항·향후기술(=tech) 감지")

    # 26. lv7 단위작업 — 상세 폼은 lv6·lv7, 부하·AI·집계는 lv6 롤업
    ck(schema.LEVEL_MAX == 7 and schema.LEVEL_LABELS.get(7) == "단위작업", "LEVEL_MAX=7 · lv7 라벨=단위작업")
    ck(schema.has_detail(6) and schema.has_detail(7), "has_detail: lv6·lv7 폼 대상")
    ck(schema.is_load_level(6) and not schema.is_load_level(7), "is_load_level: lv6 만 (집계 분모)")

    lt = schema.bootstrap()
    p4 = schema.add_node(lt, "lv3_seonjang", 4, "x", "대분류L")
    p5 = schema.add_node(lt, p4, 5, "x", "중분류L")
    c6a = schema.add_node(lt, p5, 6, "x", "세부-직접")     # 부하·기술을 lv6 에 직접
    c6b = schema.add_node(lt, p5, 6, "x", "세부-롤업")     # 자신은 비고 lv7 이 채움
    u7a = schema.add_node(lt, c6b, 7, "x", "단위-1")
    u7b = schema.add_node(lt, c6b, 7, "x", "단위-2")
    lt = schema.normalize(lt)
    ck(schema.node_map(lt["nodes"])[u7a]["level"] == 7, "lv7 깊이 재계산 (level==7)")

    schema.update_node(lt, c6a, {"tech": ["Copilot"], "work_hours": "2", "freq_unit": "주", "freq_count": "1"}, "x")
    schema.update_node(lt, u7a, {"tech": ["RPA"], "work_hours": "1", "freq_unit": "주", "freq_count": "2"}, "x")
    schema.update_node(lt, u7b, {"work_hours": "3", "freq_unit": "월", "freq_count": "1"}, "x")
    lt = schema.normalize(lt)
    s = schema.stats(lt)
    ck(s["detail_total"] == 2, f"집계 분모 = lv6 2개 (lv7 제외, 실제 {s['detail_total']})")
    ck(s["by_level"].get(7) == 2, "by_level 에 lv7 2개 표시")
    ck(s["total_hours"] == 244.0, f"부하 롤업 = lv6(104) + lv7자식(104+36)=244 (실제 {s['total_hours']})")
    ck(s["ai_yes"] == 2, "AI 롤업 — lv7 자식 기술로 부모 lv6 도 적용 카운트")
    ck(s["ai_hours"] == 244.0, f"AI 공수도 롤업 합산 (실제 {s['ai_hours']})")
    ok7, msg7 = schema.apply_move(lt, c6a, u7b, "x")
    ck(not ok7 and f"lv{schema.LEVEL_MAX}" in msg7, f"lv7 아래로는 이동 거부: {msg7}")

    # 엑셀 lv0~lv7 왕복 — lv7 노드 보존
    ck("lv7" in excel_io.LV_COLS, "excel LV_COLS 에 lv7 포함")
    rt7, _ = excel_io.parse_excel(excel_io.build_xlsx(lt, mask=False), lt)
    ck(schema.node_map(rt7["nodes"]).get(u7a, {}).get("level") == 7, "lv7 노드 엑셀 왕복 보존")

    # 취합 인원수는 lv6 기준 — lv7 은 세지 않는다(롤업 대상)
    def _mkfile7(dept, author):
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        n5 = schema.add_node(nd, n4, 5, "x", "레이더")
        n6 = schema.add_node(nd, n5, 6, "x", "레이더시험")
        n7 = schema.add_node(nd, n6, 7, "x", "전원인가")
        schema.update_node(nd, n6, {"dept": dept}, "x")
        schema.update_node(nd, n7, {"dept": dept, "work_hours": "1", "freq_unit": "주", "freq_count": "1"}, "x")
        return json.dumps({"exported_by": author, "exported_dept": dept,
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}},
                          ensure_ascii=False).encode("utf-8")

    cf = [("프로세스_A_시운전1부_20260721.json", _mkfile7("시운전1부", "A")),
          ("프로세스_B_시운전2부_20260721.json", _mkfile7("시운전2부", "B"))]
    cm7, _, _ = excel_io.collect_jsons(cf, schema.bootstrap())
    c6nodes = [n for n in cm7["nodes"] if n["level"] == 6]
    c7nodes = [n for n in cm7["nodes"] if n["level"] == 7]
    ck(len(c6nodes) == 1 and c6nodes[0].get("submit_count") == "2", "취합 인원수 lv6 기준 = 2")
    ck(len(c7nodes) == 1 and not c7nodes[0].get("submit_count"), "lv7 은 인원수 집계 안 함(롤업 대상)")

    # 27. 취합 재귀 수집 — 파일명이 상대경로(하위 폴더)여도 신원 파싱·인원 집계가 유지된다
    #     (_collect_files_from_folder 는 app.py/streamlit 의존이라 여기선 collect_jsons 계약만 검증)
    d1, a1 = excel_io._submitter_of({}, "과A/프로세스_홍길동_시운전1부_20260721.json")
    ck(d1 == "시운전1부" and a1 == "홍길동", f"상대경로 파일명에서도 부서·이름 파싱 (실제 {d1},{a1})")
    d2, a2 = excel_io._submitter_of({}, "sub\\프로세스_김철수_시운전2부_20260721.json")
    ck(d2 == "시운전2부" and a2 == "김철수", "역슬래시 상대경로도 basename 파싱")
    d3, a3 = excel_io._submitter_of({"exported_dept": "시운전3부", "exported_by": "이영희"},
                                    "무관한/경로.json")
    ck(d3 == "시운전3부" and a3 == "이영희", "봉투(exported_dept/by)가 파일명보다 우선")

    def _mkfile7r(dept, author):     # 하위 폴더에서 온 것처럼 상대경로 파일명
        nd = schema.bootstrap()
        n4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        n5 = schema.add_node(nd, n4, 5, "x", "레이더")
        n6 = schema.add_node(nd, n5, 6, "x", "레이더시험")
        schema.update_node(nd, n6, {"dept": dept, "work_hours": "1", "freq_unit": "주", "freq_count": "1"}, "x")
        return json.dumps({"exported_by": author, "exported_dept": dept,
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}},
                          ensure_ascii=False).encode("utf-8")

    # 운영 공유폴더와 같은 <부서>/<과>/ 구조. 봉투에는 **부서명**이 들어 있지만 소속의
    # 정본은 폴더이므로, 집계에 잡히는 것은 두 번째 폴더(과)여야 한다.
    rfiles = [("시운전1부/기장운전1과/프로세스_A_시운전1부_20260721.json", _mkfile7r("시운전1부", "A")),
              ("시운전2부/기장운전2과/sub/프로세스_B_시운전2부_20260721.json", _mkfile7r("시운전2부", "B"))]
    rmerged, rreports, rerr = excel_io.collect_jsons(rfiles, schema.bootstrap())
    ck(rerr == [], f"상대경로 파일 취합 전역오류 없음: {rerr}")
    rl6 = [n for n in rmerged["nodes"] if n["level"] == 6]
    ck(len(rl6) == 1 and rl6[0].get("submit_count") == "2", "하위 폴더 2건 인원수 = 2 (경로 병합)")
    ck(rl6[0].get("depts") == ["기장운전1과", "기장운전2과"],
       "소속은 봉투(부서)가 아니라 **폴더의 과** 기준")
    ck(sorted(r["filename"] for r in rreports) == ["시운전1부/기장운전1과/프로세스_A_시운전1부_20260721.json",
                                                   "시운전2부/기장운전2과/sub/프로세스_B_시운전2부_20260721.json"],
       "리포트 filename 이 상대경로(하위 폴더) 그대로 보존")

    # 28. 같은 이름 형제 중복 감지 (복사 기능 가드 — JS dupSiblings 트윈)
    dt = schema.bootstrap()
    p4 = schema.add_node(dt, "lv3_seonjang", 4, "x", "항해장비")
    p5 = schema.add_node(dt, p4, 5, "x", "레이더")
    a6 = schema.add_node(dt, p5, 6, "x", "동작시험")
    b6 = schema.add_node(dt, p5, 6, "x", "동작시험")     # 같은 부모(레이더) 아래 동명 lv6 → 중복
    p5b = schema.add_node(dt, p4, 5, "x", "자이로")
    c6 = schema.add_node(dt, p5b, 6, "x", "동작시험")    # 다른 부모(자이로) → 중복 아님
    dt = schema.normalize(dt)
    dups = schema.duplicate_siblings(dt)
    ck(len(dups) == 1 and dups[0]["name"] == "동작시험" and set(dups[0]["ids"]) == {a6, b6},
       f"동명 형제 lv6 감지(레이더 밑 2개), 다른 부모는 제외 (실제 {dups})")
    # (b) 이름 하나 바꾸면 미감지
    schema.update_node(dt, b6, {"name": "정밀시험"}, "x")
    dt = schema.normalize(dt)
    ck(schema.duplicate_siblings(dt) == [], "이름 수정하면 중복 해소")
    # (d) lv3/lv4 동명 형제는 min_level=5 라 미감지
    dd = schema.bootstrap()
    q4a = schema.add_node(dd, "lv3_seonjang", 4, "x", "같은대분류")
    q4b = schema.add_node(dd, "lv3_seonjang", 4, "x", "같은대분류")
    dd = schema.normalize(dd)
    ck(schema.duplicate_siblings(dd) == [], "lv4 동명 형제는 기본(min_level=5) 미감지")
    ck(len(schema.duplicate_siblings(dd, min_level=4)) == 1, "min_level=4 로 낮추면 lv4 동명 감지")
    # (e) 빈 이름 형제는 미감지
    de = schema.bootstrap()
    r4 = schema.add_node(de, "lv3_seonjang", 4, "x", "대분류E")
    r5 = schema.add_node(de, r4, 5, "x", "중분류E")
    schema.add_node(de, r5, 6, "x", "")
    schema.add_node(de, r5, 6, "x", "")
    de = schema.normalize(de)
    ck(schema.duplicate_siblings(de) == [], "빈 이름 형제는 중복으로 안 침")
    # (f) validate 는 동명 형제를 오류로 잡지 않는다(경고 전용, 로드/저장 거부 방지)
    ck(schema.validate(schema.normalize({**dt, "nodes":
        [dict(n) for n in dt["nodes"]] +
        [{"id": "dupx", "parent_id": p5, "level": 6, "name": "정밀시험"}]})) == [],
       "validate 는 동명 형제를 오류로 취급하지 않음")

    # 29. 폴더 기반 소속 + 부서별 제출값(submissions)
    # ── (a) 스키마 계약 ──
    ck("submissions" in schema.NODE_DEFAULTS, "submissions 가 NODE_DEFAULTS 에 있다")
    ck("submissions" not in schema.DETAIL_FIELDS,
       "submissions 는 DETAIL_FIELDS 에 없다 (자기참조·has_hidden_detail 오탐 방지)")
    ck("submissions" not in excel_io.FIELD_COLS.values(), "submissions 는 FIELD_COLS 에 없다 (역수입 금지)")
    _need = {"occur_pattern", "apply_phases", "events", "work_hours", "freq_unit", "freq_count",
             "tech", "future_tech", "automation_level", "ship_types", "special_note",
             "linked_systems", "outputs"}
    ck(_need <= set(schema.SUBMISSION_FIELDS),
       f"SUBMISSION_FIELDS 가 상세필드를 전부 담는다 (빠진 것: {sorted(_need - set(schema.SUBMISSION_FIELDS))})")
    ck(not ({"dept", "depts"} & set(schema.SUBMISSION_FIELDS)),
       "SUBMISSION_FIELDS 에 dept/depts 는 없다 (dept 는 레코드 키, depts 는 집계 산출물)")
    ck("제출과수" in excel_io.DERIVED_COLS and "제출합계공수(h)" in excel_io.DERIVED_COLS
       and "제출과수" not in excel_io.FIELD_COLS, "제출과수·제출합계공수는 DERIVED_COLS(쓰기 전용)")

    # ── (b) 폴더 → (부서, 과) ──
    _bs = chr(92)
    ck(excel_io._dept_from_path("시운전1부/기장운전1과/프로세스_A_20260819.json")
       == ("시운전1부", "기장운전1과"), "폴더 2단 → (부서, 과)")
    ck(excel_io._dept_from_path("시운전1부/프로세스_A.json") == ("시운전1부", ""), "폴더 1단 → 부서만")
    ck(excel_io._dept_from_path("프로세스_A.json") == ("", ""), "루트 직하 → 폴더 정보 없음(폴백 대상)")
    ck(excel_io._dept_from_path("부서/과/서브/f.json") == ("부서", "과"), "3단 이하 하위폴더는 무시")
    ck(excel_io._dept_from_path("시운전1부" + _bs + "기장운전1과" + _bs + "f.json")
       == ("시운전1부", "기장운전1과"), "역슬래시 경로도 동일 파싱")

    # ── (c) 폴더가 봉투를 이긴다 + 과별 값 보존 ──
    def _mkf29(author, wh, tech, dept_env="시운전3부", name="레이더시험"):
        nd = schema.bootstrap()
        q4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "항해장비")
        q5 = schema.add_node(nd, q4, 5, "x", "레이더")
        q6 = schema.add_node(nd, q5, 6, "x", name)
        schema.update_node(nd, q6, {"work_hours": wh, "freq_unit": "주", "freq_count": "3",
                                    "automation_level": "부분자동", "tech": list(tech),
                                    "occur_pattern": "상시루틴"}, "x")
        return json.dumps({"exported_by": author, "exported_dept": dept_env,
                           "nodes": schema.normalize(nd)["nodes"], "domains": {}},
                          ensure_ascii=False).encode("utf-8")

    fA29 = ("시운전1부/기장운전1과/프로세스_A_20260819.json", _mkf29("홍길동", "0.5", ["RPA"]))
    fB29 = ("시운전1부/선장운전1과/프로세스_B_20260819.json", _mkf29("김철수", "2", ["LLM", "OCR"]))
    m29, rep29, err29 = excel_io.collect_jsons([fA29, fB29], schema.bootstrap())
    ck(err29 == [], f"폴더 취합 전역오류 없음: {err29}")
    ck(sorted(r["dept"] for r in rep29) == ["기장운전1과", "선장운전1과"],
       "봉투가 시운전3부여도 **폴더의 과가 이긴다**")
    l6_29 = [n for n in m29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(l6_29["depts"] == ["기장운전1과", "선장운전1과"], "depts 가 폴더 기준 두 과")
    subs29 = l6_29["submissions"]
    ck(sorted((r["dept"], r["work_hours"]) for r in subs29)
       == [("기장운전1과", "0.5"), ("선장운전1과", "2")],
       "★ 과마다 다른 소요시간이 그대로 보존된다 (이 작업의 핵심 요구사항)")
    ck([r["dept"] for r in subs29 if "LLM" in (r.get("tech") or [])] == ["선장운전1과"],
       "과별 활용기술도 분리 보존")
    ck(sum(r["count"] for r in subs29) == int(l6_29["submit_count"]),
       "sum(rec.count) == submit_count 불변식")
    # 이름 정책이 뒤집혔다: 같은 과에서 값이 갈릴 때 누가 낸 건지 알아야 고칠 수 있어서,
    # **레코드에는 원본을 저장하고 출력에서만 마스킹**한다(pii.py 원칙 · updated_by 와 동급).
    ck(subs29[0].get("author") == "홍길동", "제출자 이름을 레코드에 저장한다 (출력은 마스킹)")
    _re29 = schema.normalize({"nodes": [dict(n) for n in m29["nodes"]], "domains": dict(m29["domains"])})
    _re6 = [n for n in _re29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck([r.get("author") for r in _re6["submissions"]] == [r.get("author") for r in subs29],
       "★ normalize 를 다시 통과해도 author 가 살아남는다 (rec 재구성에서 증발하기 쉬운 지점)")
    ck(pii.mask_name("홍길동") == "홍*동", "mask_name 은 가운데를 가린다")
    ck(not any("annual_hours" in r for r in subs29), "곱한 값(annual_hours)은 저장하지 않는다")
    ck(schema.annual_hours(subs29[0]) > 0, "레코드에도 annual_hours 계산이 그대로 동작한다")
    ck(schema.submission_of(l6_29, "선장운전1과")["work_hours"] == "2", "submission_of 로 과별 값 조회")

    # 폴더 없음 → 봉투 폴백 / 봉투도 없음 → 파일명 폴백 (브라우저 다중업로드 방어)
    _, repf29, _ = excel_io.collect_jsons(
        [("프로세스_A_20260819.json", _mkf29("홍길동", "1", []))], schema.bootstrap())
    ck(repf29[0]["dept"] == "시운전3부", "폴더가 없으면 봉투로 폴백")
    _nb29 = json.dumps({"nodes": json.loads(_mkf29("x", "1", []).decode("utf-8"))["nodes"],
                        "domains": {}}, ensure_ascii=False).encode("utf-8")
    _, repn29, _ = excel_io.collect_jsons(
        [("프로세스_홍길동_시운전2부_20260819.json", _nb29)], schema.bootstrap())
    ck(repn29[0]["dept"] == "시운전2부", "봉투도 없으면 파일명으로 폴백")

    # ── (d) 미매핑 폴더명 = 원문 보존 / 부서 불일치 경고 ──
    m29u, _, _ = excel_io.collect_jsons(
        [("신설부/신설과/프로세스_A_20260819.json", _mkf29("홍길동", "1", []))], schema.bootstrap())
    l6u29 = [n for n in m29u["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(l6u29["depts"] == ["신설과"], "매핑에 없는 폴더명은 원문 그대로 (값 유실 금지)")
    ck(schema.dept_parent("신설과") == "미분류", "미매핑 과는 미분류로 롤업")
    ck("신설과" in (excel_io.unknown_domain_values(m29u).get("dept") or []),
       "미등록 과를 unknown_domain_values 가 잡는다 (도메인 승인 흐름 재사용)")
    _, repw29, _ = excel_io.collect_jsons(
        [("시운전2부/기장운전1과/프로세스_A_20260819.json", _mkf29("홍길동", "1", []))],
        schema.bootstrap())
    ck(bool(repw29[0]["warn"]) and not repw29[0]["errors"],
       "폴더 부서 불일치는 warn 이지 errors 가 아니다 (파일이 통째로 제외되면 안 된다)")

    # ── (e) 같은 과 합산 / 재취합 멱등 · 교체 · 미스캔 경로 보존 ──
    mS29, _, _ = excel_io.collect_jsons(
        [("시운전1부/기장운전1과/프로세스_A_20260819.json", _mkf29("홍길동", "0.5", ["RPA"])),
         ("시운전1부/기장운전1과/프로세스_B_20260819.json", _mkf29("김철수", "0.5", ["RPA"]))],
        schema.bootstrap())
    l6S29 = [n for n in mS29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    # 서명에 author 가 들어가므로 **같은 과·같은 값이라도 사람이 다르면 레코드가 갈린다.**
    # 이게 편집의 전제다 — 한 행을 고쳐 다른 행과 값이 같아져도 합쳐져 사라지지 않는다.
    ck(len(l6S29["submissions"]) == 2 and all(r["count"] == 1 for r in l6S29["submissions"]),
       f"같은 과·같은 값이라도 사람이 다르면 레코드 2건·각 1명 (실제 {len(l6S29['submissions'])}건)")
    ck({r["dept"] for r in l6S29["submissions"]} == {"기장운전1과"}, "두 레코드의 과는 같다")
    ck(sorted(r.get("author") for r in l6S29["submissions"]) == ["김철수", "홍길동"],
       "두 레코드의 제출자는 다르다")
    ck(sum(r["count"] for r in l6S29["submissions"]) == int(l6S29["submit_count"]),
       "사람별로 갈려도 sum(count) == submit_count 는 유지된다")
    # 병합 코드는 죽지 않았다 — 재정규화 멱등 가드로 남는다(같은 리스트를 두 번 돌려도 안 늘어남)
    _twice = schema.normalize({"nodes": [dict(n) for n in mS29["nodes"]], "domains": dict(mS29["domains"])})
    _t6 = [n for n in _twice["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(len(_t6["submissions"]) == 2, "재정규화해도 레코드가 늘지 않는다 (멱등 가드 생존)")
    m29b, _, _ = excel_io.collect_jsons([fA29, fB29], m29)
    l6b29 = [n for n in m29b["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(l6b29["submissions"] == subs29, "재취합 멱등 — submissions 불변")
    m29c, _, _ = excel_io.collect_jsons(
        [("시운전2부/기장운전2과/프로세스_A_20260819.json", _mkf29("홍길동", "0.5", ["RPA"]))], m29)
    l6c29 = [n for n in m29c["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck([r["dept"] for r in l6c29["submissions"]] == ["기장운전2과"],
       "재취합은 submissions 를 **교체**한다 (누적하면 정정이 원천 불가 — depts 와 같은 원칙)")
    fD29 = ("시운전1부/전장운전1과/프로세스_D_20260819.json",
            _mkf29("이영희", "3", [], name="자이로시험"))
    mD29, _, _ = excel_io.collect_jsons([fA29, fD29], schema.bootstrap())
    mD29b, _, _ = excel_io.collect_jsons([fA29], mD29)          # 레이더시험만 재스캔
    gy29 = [n for n in mD29b["nodes"] if n["name"] == "자이로시험"][0]
    ck([r["dept"] for r in gy29["submissions"]] == ["전장운전1과"],
       "이번 스캔에 **없는** 경로의 submissions 는 건드리지 않는다")

    # ── (f) 엑셀 왕복 · 제출상세 시트 · KPI 불변 ──
    rt29, _ = excel_io.parse_excel(excel_io.build_xlsx(m29, mask=False), m29)
    r6_29 = [n for n in rt29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(r6_29.get("submissions") == subs29,
       "★ 엑셀 왕복 후에도 submissions 생존 (parse_excel 이 base 에서 보존)")
    ck(len(excel_io._submission_df(m29)) == sum(len(schema.submissions_of(n)) for n in m29["nodes"]),
       "제출상세 시트 행수 == Σ len(submissions)")
    ck(excel_io._submission_df(schema.bootstrap()).empty, "취합 전 트리는 제출상세 시트를 만들지 않는다")
    ck("제출자" in excel_io.SUBMIT_COLS, "제출상세 시트에 제출자 열이 있다")
    _don = excel_io._submission_df(m29, mask=True)
    _doff = excel_io._submission_df(m29, mask=False)
    ck("홍길동" not in list(_don["제출자"]) and "홍*동" in list(_don["제출자"]),
       "★ mask=True 면 제출자가 마스킹된다")
    ck("홍길동" in list(_doff["제출자"]), "mask=False 면 원본 (편집·대조용)")
    ck("홍길동".encode("utf-8") not in excel_io.build_xlsx(m29, mask=True),
       "★ build_xlsx(mask=True) 산출물 어디에도 원본 이름이 없다 (시트 간 마스킹 정합)")
    # 옛 레코드(author 없음)도 통과해야 한다
    _old29 = schema.normalize({"nodes": [{"id": "o9", "parent_id": "__root__", "level": 6, "name": "옛",
                                          "submissions": [{"dept": "기장운전1과", "work_hours": "1"}]}]})
    ck(_old29["nodes"][0]["submissions"][0].get("author") is None,
       "author 없는 옛 레코드도 그대로 통과한다 (키를 억지로 만들지 않는다)")
    # submit_detail 은 취합 요약이라 **이름이 없어야 한다** — chat_context 를 타고 LLM 으로 나간다
    ck("홍길동" not in (l6_29.get("submit_detail") or ""),
       "★ submit_detail(LLM 컨텍스트 경유)에는 이름을 넣지 않는다")
    ck(schema.detail_summary is not None and "_detail_summary" in dir(excel_io),
       "detail_summary 는 schema 가 갖고 excel_io 는 위임한다")
    # 취합 산출물 2종은 이제 **레코드로부터 파생**된다 — 레코드를 고치면 요약이 따라와야 한다.
    _ed29 = copy.deepcopy(m29)
    _e6 = [n for n in _ed29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    _e6["submissions"][0]["work_hours"] = "9.5"
    _ed29 = schema.normalize(_ed29)
    _e6 = [n for n in _ed29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck("소요 9.5h" in _e6["submit_detail"], "레코드를 고치면 submit_detail 이 따라온다")
    ck("홍길동" not in _e6["submit_detail"], "재생성된 요약에도 이름이 없다")
    ck(schema.stats(_ed29) == schema.stats(m29),
       "★ 레코드를 고쳐도 KPI 는 불변 — stats 는 submissions 를 읽지 않는다")
    ck("9.5" in list(excel_io._submission_df(_ed29)["1회소요시간(h)"].astype(str)),
       "제출상세 시트에도 편집값이 반영된다")
    # 1건만 남기면 N≥2 규칙에 걸려 배지가 사라져야 한다
    _dl29 = copy.deepcopy(m29)
    _d6 = [n for n in _dl29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    _d6["submissions"] = _d6["submissions"][:1]
    _d6 = [n for n in schema.normalize(_dl29)["nodes"]
           if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(_d6["submit_count"] == "", "레코드가 1건이면 submit_count 는 비운다 (N≥2 규칙)")
    # submissions 가 없는 옛 트리는 두 필드를 그대로 보존한다
    _old4 = schema.normalize({"nodes": [{"id": "o4", "parent_id": "__root__", "level": 6, "name": "옛",
                                         "submit_count": "5", "submit_detail": "옛 요약"}]})
    ck(_old4["nodes"][0]["submit_count"] == "5" and _old4["nodes"][0]["submit_detail"] == "옛 요약",
       "submissions 없는 옛 트리는 취합 산출물을 건드리지 않는다")
    m29_nosub = copy.deepcopy(m29)
    for _n29 in m29_nosub["nodes"]:
        _n29["submissions"] = []
    ck(schema.stats(m29) == schema.stats(m29_nosub), "stats(KPI)는 submissions 도입 전후 동일")

    # ── (g) normalize 방어 · 개인 JSON 이어붙이기 ──
    bad29 = schema.normalize({"nodes": [{"id": "b1", "parent_id": "__root__", "level": 6, "name": "b",
                                         "submissions": ["문자열", {"work_hours": "1"}, 3,
                                                         {"dept": "기장운전1과", "work_hours": "1"}]}]})
    ck([r["dept"] for r in bad29["nodes"][0]["submissions"]] == ["기장운전1과"],
       "깨진 레코드(문자열·숫자·소속 없음)는 버린다")
    ck("has_ai_agent" not in bad29["nodes"][0]["submissions"][0], "빈 값 키는 저장하지 않는다(희소 저장)")
    _solo29 = json.dumps({"nodes": [dict(n, submissions=[], submit_count="", submit_detail="")
                                    for n in m29["nodes"]], "domains": {}},
                         ensure_ascii=False).encode("utf-8")
    pj29, _ = excel_io.parse_json(_solo29, m29)
    p6_29 = [n for n in pj29["nodes"] if n["level"] == 6 and n["name"] == "레이더시험"][0]
    ck(p6_29.get("submissions") == subs29,
       "개인 JSON 이어붙이기가 과별 제출값을 지우지 않는다 (취합 산출물은 base 보존)")

    # 30. 이름 붙인 보관본 — 같은 이름은 덮지 않고 버전으로 쌓인다
    sv = schema.bootstrap()
    schema.add_node(sv, "lv3_seonjang", 4, "x", "보관테스트")
    sv = schema.normalize(sv)
    sv["rev"] = 7

    ck(pc.get_saves_dir().name == "saves", "보관본 폴더는 saves/")
    ck(pc.get_saves_dir() != pc.get_history_dir(),
       "보관본 폴더는 history/ 와 분리 — prune_history 의 자동 삭제 대상이 아니다")

    ck(not store.save_named(sv, "1차", "  ").ok, "저장자 없으면 보관 거부")
    ck(not store.save_named(sv, "   ", "관리자").ok, "이름이 비면 보관 거부")

    r30a = store.save_named(sv, "2026 상반기안", "관리자")
    r30b = store.save_named(sv, "2026 상반기안", "관리자")
    r30c = store.save_named(sv, "2026 상반기안", "이정호")
    ck(r30a.ok and r30b.ok and r30c.ok, "같은 이름 3회 보관이 모두 성공한다 (덮어쓰기 아님)")
    ck((r30a.version, r30b.version, r30c.version) == (1, 2, 3),
       f"버전이 1,2,3 으로 쌓인다 (실제 {(r30a.version, r30b.version, r30c.version)})")
    ck((pc.get_saves_dir() / "2026 상반기안").is_dir(), "이름은 폴더가 된다 (파일명 파싱 불필요)")

    lst30 = store.list_saves()
    ck(len(lst30) == 1, f"계보는 1개로 묶인다 (실제 {len(lst30)})")
    g30 = lst30[0]
    ck(g30["name"] == "2026 상반기안" and g30["n_versions"] == 3 and g30["latest_version"] == 3,
       "목록은 이름 단위 + 버전 수·최신 버전")
    ck([v["version"] for v in g30["versions"]] == [3, 2, 1], "버전 목록은 최신순")
    ck(g30["author"] == "이정호", "대표 저장자는 **최신 버전** 것")
    ck(g30["versions"][0]["rev"] == 7, "보관본은 rev 를 올리지 않는다 (정본과 번호 경합 방지)")

    ck(store.load_named("2026 상반기안").get("saved_version") == 3, "version 없이 부르면 최신")
    ck(store.load_named("2026 상반기안", 1).get("saved_version") == 1, "특정 버전 지정 로드")
    ck(store.load_named("2026 상반기안", 99) is None, "없는 버전은 None")
    ck(store.load_named("없는이름") is None, "없는 계보는 None")

    # 경로 탈출 — _safe_label 이 . 과 - 를 남기므로 _group_dir 의 한 겹이 반드시 필요하다
    ck(store.load_named("..") is None and store.delete_named("..") is False, "'..' 경로 탈출 차단")
    ck(store._group_dir("..") is None and store._group_dir(".hidden") is None, "점으로 시작하는 이름 거부")
    ck(store._safe_label("a/b\\c") == "a_b_c", "경로 구분자는 이름에 못 들어간다")
    ck(len(store._safe_label("가" * 200)) <= 60, "이름 길이 상한")

    # 보관은 정본을 건드리지 않는다
    _, rev_before30, _ = store.disk_stat()
    store.save_named(sv, "정본무관", "관리자")
    _, rev_after30, _ = store.disk_stat()
    ck(rev_before30 == rev_after30, "보관해도 정본 rev 는 그대로 (사본이지 저장이 아니다)")

    # 버전 하나만 삭제 / 계보 통째 삭제
    ck(store.delete_named("2026 상반기안", 2), "버전 하나 삭제")
    _g30 = [g for g in store.list_saves() if g["name"] == "2026 상반기안"][0]
    ck([v["version"] for v in _g30["versions"]] == [3, 1], "지운 버전만 빠지고 나머지는 남는다")
    ck(store.save_named(sv, "2026 상반기안", "관리자").version == 4,
       "버전 번호는 재사용하지 않는다 (최대값 +1)")
    ck(store.delete_named("2026 상반기안"), "계보 통째 삭제")
    ck([g["name"] for g in store.list_saves()] == ["정본무관"], "삭제 후 목록에서 계보가 빠진다")
    ck(not (pc.get_saves_dir() / "2026 상반기안").exists(), "폴더까지 지워진다")

    # 31. AI 카운트 제외 기술 (domains["tech_no_ai"])
    ck(schema.DEFAULT_DOMAINS["tech_no_ai"] == [],
       "기본값은 빈 목록 — 제외 없음 = 기존과 동일(마이그레이션 무손실)")
    ck(schema.DOMAIN_LABELS.get("tech_no_ai") == "AI 카운트 제외 기술", "도메인 라벨 등록")
    ck("tech_no_ai" not in (excel_io.unknown_domain_values(schema.bootstrap()) or {}),
       "노드에서 값을 수집하는 축이 아니다 (unknown_domain_values 에 넣지 않는다)")

    def _mk31(no_ai=None):
        nd = schema.bootstrap()
        b4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "AI테스트")
        b5 = schema.add_node(nd, b4, 5, "x", "중")
        a1 = schema.add_node(nd, b5, 6, "x", "SAP만")
        a2 = schema.add_node(nd, b5, 6, "x", "SAP와LLM")
        a3 = schema.add_node(nd, b5, 6, "x", "기술없음")
        schema.update_node(nd, a1, {"tech": ["SAP"]}, "x")
        schema.update_node(nd, a2, {"tech": ["SAP", "LLM"]}, "x")
        nd.setdefault("domains", {})["tech"] = ["SAP", "LLM"]
        if no_ai is not None:
            nd["domains"]["tech_no_ai"] = list(no_ai)
        return schema.normalize(nd), a1, a2, a3

    base31, _, _, _ = _mk31()
    off31, id_sap, id_both, _ = _mk31(["SAP"])   # id 는 **이 트리 것**을 써야 한다(트리마다 새 uuid)
    s_base, s_off = schema.stats(base31), schema.stats(off31)
    ck(s_base["ai_yes"] == 2, f"제외 없으면 SAP·LLM 둘 다 AI (실제 {s_base['ai_yes']})")
    ck(s_off["ai_yes"] == 1, f"SAP 를 제외하면 'SAP만' 이 빠진다 (실제 {s_off['ai_yes']})")
    nmap31 = schema.node_map(off31["nodes"])
    ck(nmap31[id_sap]["has_ai_agent"] is False, "제외 기술만 가진 lv6 은 AI 미적용")
    ck(nmap31[id_both]["has_ai_agent"] is True, "제외 기술 + AI 기술을 함께 가지면 AI 적용")
    ck(s_base["by_tech_now"] == s_off["by_tech_now"],
       "★ 활용기술 사용량 집계는 **불변** — AI 여부와 축이 다르다(SAP 가 계속 잡히는 게 정상)")

    # 옛 트리(도메인에 키 자체가 없음) → 무손실. 상수로 박지 말고 '제외 비운 사본' 과 비교한다.
    legacy31 = schema.normalize({"nodes": [dict(n) for n in base31["nodes"]],
                                 "domains": {k: list(v) for k, v in base31["domains"].items()
                                             if k != "tech_no_ai"}})
    ck(schema.stats(legacy31)["ai_yes"] == s_base["ai_yes"],
       "tech_no_ai 키가 없는 옛 저장본도 AI 지표가 그대로 (무손실 마이그레이션)")
    ck(legacy31["domains"]["tech_no_ai"] == [], "normalize 가 빈 목록으로 백필한다")

    # 전부 체크 해제(= tech 전체가 제외목록) 상태가 로드마다 되살아나지 않아야 한다
    allof31, _, _, _ = _mk31(["SAP", "LLM"])
    ck(schema.stats(allof31)["ai_yes"] == 0, "전부 제외하면 AI 0건")
    ck(schema.normalize(allof31)["domains"]["tech_no_ai"] == ["SAP", "LLM"],
       "'전부 제외' 는 빈 목록과 다르다 — 재정규화에도 유지된다")

    # lv7 자식만 제외기술을 가져도 부모 lv6 롤업에 반영
    r31 = schema.bootstrap()
    c4 = schema.add_node(r31, "lv3_seonjang", 4, "x", "롤업")
    c5 = schema.add_node(r31, c4, 5, "x", "중")
    c6 = schema.add_node(r31, c5, 6, "x", "부모")
    c7 = schema.add_node(r31, c6, 7, "x", "자식")
    schema.update_node(r31, c7, {"tech": ["SAP"]}, "x")
    r31.setdefault("domains", {})["tech"] = ["SAP"]
    ck(schema.stats(schema.normalize(r31))["ai_yes"] == 1, "제외 전: lv7 기술이 부모 lv6 으로 롤업")
    r31["domains"]["tech_no_ai"] = ["SAP"]
    ck(schema.stats(schema.normalize(r31))["ai_yes"] == 0, "제외 후: 롤업에서도 빠진다")

    # 과별 제출값 레코드도 같은 규칙을 타야 한다 (안 그러면 표·엑셀이 노드와 갈린다)
    rec31 = schema.normalize({
        "nodes": [{"id": "s1", "parent_id": "__root__", "level": 6, "name": "s",
                   "submissions": [{"dept": "기장운전1과", "tech": ["SAP"]},
                                   {"dept": "선장운전1과", "tech": ["SAP", "LLM"]}]}],
        "domains": {"tech": ["SAP", "LLM"], "tech_no_ai": ["SAP"]}})
    subs31 = rec31["nodes"][0]["submissions"]
    ck([bool(r.get("has_ai_agent")) for r in subs31] == [False, True],
       "레코드의 AI 파생도 제외목록을 따른다")

    # 엑셀 왕복에서 도메인 키가 살아남는다
    rt31, _ = excel_io.parse_excel(excel_io.build_xlsx(off31, mask=False), off31)
    ck(rt31["domains"].get("tech_no_ai") == ["SAP"], "엑셀 왕복 후에도 tech_no_ai 생존")

    # 32. 부서 미지정 제출은 취합에서 제외
    def _mk32(env_dept=None, author=None):
        nd = schema.bootstrap()
        w4 = schema.add_node(nd, "lv3_seonjang", 4, "x", "미지정테스트")
        w5 = schema.add_node(nd, w4, 5, "x", "중")
        w6 = schema.add_node(nd, w5, 6, "x", "미지정업무")
        schema.update_node(nd, w6, {"work_hours": "1", "freq_unit": "주", "freq_count": "1"}, "x")
        pay = {"schema_version": 1, "nodes": schema.normalize(nd)["nodes"], "domains": {}}
        if env_dept:
            pay["exported_dept"] = env_dept
        if author:
            pay["exported_by"] = author
        return json.dumps(pay, ensure_ascii=False).encode("utf-8")

    # 폴더도 봉투도 파일명도 없다 → 제외
    m32, rep32, _ = excel_io.collect_jsons([("아무이름.json", _mk32())], schema.bootstrap())
    ck(len(rep32) == 1 and "소속을 알 수 없어 제외" in rep32[0]["errors"],
       f"소속 미상 파일은 취합에서 제외하고 사유를 남긴다 (실제 {rep32[0]['errors'][:30]})")
    ck(not any(n.get("name") == "미지정업무" for n in m32["nodes"]),
       "제외된 파일의 노드는 정본에 들어가지 않는다")
    ck("폴더" in rep32[0]["errors"] and "파일명" in rep32[0]["errors"],
       "사유에 고치는 방법이 적혀 있다 (조용히 버리지 않는다)")
    ck(not any("미상" in (n.get("depts") or []) for n in m32["nodes"]),
       "'미상' 이라는 유령 과가 트리에 남지 않는다")

    # 셋 중 하나라도 있으면 정상 취합돼야 한다 — 정상 파일까지 걸러내면 안 된다
    mF32, _, _ = excel_io.collect_jsons(
        [("시운전1부/기장운전1과/아무이름.json", _mk32())], schema.bootstrap())     # 폴더만
    ck(any(n.get("name") == "미지정업무" for n in mF32["nodes"]), "폴더가 있으면 정상 취합")
    mE32, _, _ = excel_io.collect_jsons(
        [("아무이름.json", _mk32(env_dept="기장운전1과"))], schema.bootstrap())      # 봉투만
    ck(any(n.get("name") == "미지정업무" for n in mE32["nodes"]), "봉투가 있으면 정상 취합")
    mN32, _, _ = excel_io.collect_jsons(
        [("프로세스_홍길동_기장운전1과_20260819.json", _mk32())], schema.bootstrap())  # 파일명만
    ck(any(n.get("name") == "미지정업무" for n in mN32["nodes"]), "파일명 규약이 맞으면 정상 취합")

    print()
    if _fails:
        print(f"=== {len(_fails)}/{_n} FAILED ===")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"=== ALL {_n} PASSED ===")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
