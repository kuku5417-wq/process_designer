"""_smoke.py — schema/store 회귀검증 (Streamlit 없이 실행).

임시 폴더를 PROCESS_DATA_PATH 로 지정해 실제 파일 저장까지 검증한다.
사용: python _smoke.py
"""
from __future__ import annotations

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
    schema.update_node(d, lv6, {"has_ai_agent": True, "tech": ["LLM", "OCR"], "owner": "홍길동"}, "tester")
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
    schema.update_node(d, w6, {"has_ai_agent": True, "tech": ["LLM"], "owner": "홍길동",
                               "dept": "시운전1부", "automation_level": "부분자동",
                               "frequency": "주 1회", "outputs": "보고서"}, "tester")
    ck(schema.node_map(d["nodes"])[w6]["level"] == 6, "보존확인 노드 lv6")
    ck(not schema.has_hidden_detail(schema.node_map(d["nodes"])[w6]), "lv6 은 숨은 값이 아님")

    ok, msg = schema.apply_move(d, w6, w4, "tester")      # lv6 -> lv5 로 승격
    n6 = schema.node_map(d["nodes"])[w6]
    ck(ok and n6["level"] == 5, f"lv6 을 lv5 로 승격: {msg}")
    ck(n6["owner"] == "홍길동" and n6["tech"] == ["LLM"] and n6["has_ai_agent"] is True,
       "승격해도 상세 값 보존 (삭제 아님)")
    ck(schema.has_hidden_detail(n6), "승격된 카드에 숨은 상세 값이 있음을 감지")

    ok, _ = schema.apply_move(d, w6, w5, "tester")        # 다시 lv6 으로
    n6 = schema.node_map(d["nodes"])[w6]
    ck(ok and n6["level"] == 6 and n6["owner"] == "홍길동", "다시 lv6 으로 내리면 값이 되살아남")

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
    ck(not r_noauthor.ok and "작성자" in r_noauthor.error, "작성자 미입력 저장 거부")

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
        ck(l6["owner"] == "김철수" and l6["dept"] == "시운전1부", "lv6 에 작성자 신원이 실려 옴")
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
    ck(len(schema.DEPT_TREE) == 7, f"DEPT_TREE 부서 7개 (실제 {len(schema.DEPT_TREE)})")
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
