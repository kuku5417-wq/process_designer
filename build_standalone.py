"""build_standalone.py — 개인 배포용 단일 HTML 생성.

frontend/index.html + frontend/sortable.min.js 를 **파일 하나**로 묶고 개인 모드 플래그를
주입한다. 팀원은 그 파일 하나만 받아 더블클릭하면 되고, 서버·파이썬·인터넷이 필요 없다.

왜 빌드가 필요한가 — 두 가지 다 런타임에는 할 수 없다:
  1. `window.__PD_SOLO__` 주입. file:// 감지로 대신하면 디자인 단독 미리보기(seed()/MOCK_*)가
     개인 모드로 오인돼 죽는다. 쿼리스트링(?mode=solo)은 더블클릭으로 못 연다.
  2. sortable.min.js 인라인. 2개 파일로 배포하면 사용자가 zip 을 안 풀고 index.html 만
     더블클릭했을 때 Sortable 이 없어 **드래그만 조용히 안 되는** 상태로 뜬다.

★ UI 소스는 frontend/index.html **한 벌뿐**이다. 여기서 복사본을 만들지 않는다.

사용:
  python build_standalone.py                     범용 HTML (lv3 시드) → dist/프로세스설계_개인작성용.html
  python build_standalone.py --seed 골격.json     과별 HTML 1개 (골격을 기본 시드로 주입)
  python build_standalone.py --seeds <폴더>        폴더의 프로세스_*.json 마다 과별 HTML 일괄 생성
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
FRONTEND = BASE / "frontend"
DIST = BASE / "dist"

_SCRIPT_TAG = '<script src="./sortable.min.js"></script>'


def _dept_from(payload: dict, filename: str) -> str:
    """제출 골격에서 과(부서/과) 추출 — 봉투 exported_dept 우선, 없으면 파일명 3번째 토큰.
    (excel_io._submitter_of 규칙과 동일. pandas 의존 없이 여기서 최소 재현.)"""
    dept = str(payload.get("exported_dept") or "").strip()
    if not dept and filename:
        parts = filename.rsplit(".", 1)[0].split("_")
        if len(parts) >= 4 and parts[0].startswith("프로세스"):
            dept = parts[2]
    return dept


def _safe_json(obj) -> str:
    """<script> 안에 넣을 JSON — </script> 로 스크립트가 끊기지 않게 </ 를 <\\/ 로 이스케이프."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build(seed: dict | None = None, out_name: str | None = None) -> Path:
    """단일 HTML 생성. seed 가 있으면 window.__PD_SEED__ 로 기본 골격을 주입한다."""
    html_path = FRONTEND / "index.html"
    js_path = FRONTEND / "sortable.min.js"
    for p in (html_path, js_path):
        if not p.exists():
            raise SystemExit(f"[FAIL] 없는 파일: {p}")

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    if _SCRIPT_TAG not in html:
        raise SystemExit(f"[FAIL] index.html 에서 {_SCRIPT_TAG} 를 찾지 못했습니다.\n"
                         "       (태그가 바뀌었다면 이 스크립트의 _SCRIPT_TAG 도 함께 고칠 것)")
    if "</script>" in js.lower():
        raise SystemExit("[FAIL] sortable.min.js 에 </script> 가 들어 있어 인라인할 수 없습니다.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    seed_line = ""
    if seed is not None:
        seed_line = f"window.__PD_SEED__ = {_safe_json(seed)};\n"
    inject = (
        "<script>\n"
        "/* build_standalone.py 주입 — 개인 배포판 표식.\n"
        "   이 플래그가 없으면 index.html 은 기존대로(Streamlit 컴포넌트/디자인 미리보기) 돈다. */\n"
        f'window.__PD_SOLO__ = true; window.__PD_BUILD__ = "{stamp}";\n'
        f"{seed_line}"
        "</script>\n"
        # str.replace 를 쓴다 — re.sub 는 minify 된 JS 의 백슬래시를 이스케이프로 해석해 깨뜨린다
        "<script>\n" + js + "\n</script>"
    )
    out_html = html.replace(_SCRIPT_TAG, inject, 1)

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / (out_name or "프로세스설계_개인작성용.html")
    out.write_text(out_html, encoding="utf-8")
    return out


def _load_seed(path: Path) -> tuple[dict, str]:
    """골격 JSON 로드 → (seed, 과). seed 는 __PD_SEED__ 로 주입할 dict."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise SystemExit(f"[FAIL] 골격 JSON 이 아닙니다 (nodes 배열 없음): {path.name}")
    dept = _dept_from(payload, path.name)
    seed = {"nodes": payload["nodes"], "dept": dept,
            "domains": payload.get("domains")}
    return seed, dept


def _report(out: Path, label: str = "") -> None:
    txt = out.read_text(encoding="utf-8")
    checks = [
        ("외부 스크립트 참조 없음 (파일 하나로 완결)", 'src="./sortable.min.js"' not in txt),
        ("Sortable 인라인됨", "Sortable" in txt),
        ("개인 모드 플래그 주입됨", "window.__PD_SOLO__ = true" in txt),
    ]
    for lbl, passed in checks:
        if not passed:
            print(f"  [FAIL] {out.name}: {lbl}")
    print(f"  [OK] {out.name}  ({out.stat().st_size:,} bytes){('  · ' + label) if label else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description="개인 배포판 HTML 빌드")
    ap.add_argument("--seed", type=str, help="과별 골격 JSON 1개 → 그 과 HTML")
    ap.add_argument("--seeds", type=str, help="폴더의 프로세스_*.json 마다 과별 HTML 일괄 생성")
    ap.add_argument("--out", type=str, help="--seed 와 함께: 출력 파일명")
    args = ap.parse_args()

    print("=" * 60)
    print("  개인 배포판 빌드")
    print("=" * 60)

    if args.seeds:
        folder = Path(args.seeds)
        if not folder.is_dir():
            raise SystemExit(f"[FAIL] 폴더 없음: {folder}")
        files = sorted(folder.glob("*.json"))
        if not files:
            raise SystemExit(f"[FAIL] .json 골격이 없습니다: {folder}")
        made = 0
        for fp in files:
            seed, dept = _load_seed(fp)
            name = f"프로세스설계_{dept or fp.stem}.html"
            out = build(seed=seed, out_name=name)
            _report(out, f"과: {dept or '(미상)'}, 노드 {len(seed['nodes'])}")
            made += 1
        print(f"\n  총 {made}개 과별 HTML 생성 → {DIST}")
        return 0

    if args.seed:
        seed, dept = _load_seed(Path(args.seed))
        name = args.out or f"프로세스설계_{dept or '골격'}.html"
        out = build(seed=seed, out_name=name)
        _report(out, f"과: {dept or '(미상)'}, 노드 {len(seed['nodes'])}")
        print("\n  이 파일을 해당 과에 전달하면 됩니다 (더블클릭으로 열림).")
        return 0

    # 무인자 — 기존 범용 HTML
    out = build()
    _report(out)
    print(f"\n  산출물: {out}")
    print("  이 파일 하나를 팀원에게 전달하면 됩니다 (더블클릭으로 열림).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
