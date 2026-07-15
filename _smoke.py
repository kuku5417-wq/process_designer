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
    ck(not ok and "lv6" in msg, f"깊이 초과 이동 거부: {msg}")

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

    # 7. 손상 데이터 정규화 (고아 / 사이클 / 결측)
    bad = {"nodes": [
        {"id": "a", "parent_id": "없음", "name": "고아"},
        {"id": "b", "parent_id": "c", "name": "순환B", "level": 4},
        {"id": "c", "parent_id": "b", "name": "순환C", "level": 4},
    ]}
    bad = schema.normalize(bad)
    ck(schema.validate(bad) == [], f"손상 데이터 정규화 후 validate 통과: {schema.validate(bad)}")
    ck(schema.node_map(bad["nodes"])["a"]["parent_id"] == schema.ROOT_ID, "고아 노드를 ROOT 로 구제 (삭제 아님)")

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
