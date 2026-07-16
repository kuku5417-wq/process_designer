"""build_standalone.py — 개인 배포용 단일 HTML 생성.

frontend/index.html + frontend/sortable.min.js 를 **파일 하나**로 묶고 개인 모드 플래그를
주입한다. 팀원은 그 파일 하나만 받아 더블클릭하면 되고, 서버·파이썬·인터넷이 필요 없다.

왜 빌드가 필요한가 — 두 가지 다 런타임에는 할 수 없다:
  1. `window.__PD_SOLO__` 주입. file:// 감지로 대신하면 디자인 단독 미리보기(seed()/MOCK_*)가
     개인 모드로 오인돼 죽는다. 쿼리스트링(?mode=solo)은 더블클릭으로 못 연다.
  2. sortable.min.js 인라인. 2개 파일로 배포하면 사용자가 zip 을 안 풀고 index.html 만
     더블클릭했을 때 Sortable 이 없어 **드래그만 조용히 안 되는** 상태로 뜬다.

★ UI 소스는 frontend/index.html **한 벌뿐**이다. 여기서 복사본을 만들지 않는다 —
  복사하면 UI 두 벌을 영원히 중복 유지해야 한다.

사용: python build_standalone.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
FRONTEND = BASE / "frontend"
DIST = BASE / "dist"

_SCRIPT_TAG = '<script src="./sortable.min.js"></script>'


def build() -> Path:
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
    # 인라인한 JS 안에 </script> 가 있으면 그 자리에서 스크립트가 끊긴다
    if "</script>" in js.lower():
        raise SystemExit("[FAIL] sortable.min.js 에 </script> 가 들어 있어 인라인할 수 없습니다.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    inject = (
        "<script>\n"
        "/* build_standalone.py 주입 — 개인 배포판 표식.\n"
        "   이 플래그가 없으면 index.html 은 기존대로(Streamlit 컴포넌트/디자인 미리보기) 돈다. */\n"
        f'window.__PD_SOLO__ = true; window.__PD_BUILD__ = "{stamp}";\n'
        "</script>\n"
        # str.replace 를 쓴다 — re.sub 는 minify 된 JS 의 백슬래시를 이스케이프로 해석해 깨뜨린다
        "<script>\n" + js + "\n</script>"
    )
    out_html = html.replace(_SCRIPT_TAG, inject, 1)

    DIST.mkdir(parents=True, exist_ok=True)
    # 파일명은 **고정**한다. 타임스탬프를 넣으면 재빌드마다 새 파일이 생겨 저장소에 옛
    # 산출물이 쌓이고, 팀원에게 줄 링크도 매번 바뀐다. 빌드 시각은 파일 안(__PD_BUILD__)에 있다.
    out = DIST / "프로세스설계_개인작성용.html"
    out.write_text(out_html, encoding="utf-8")
    return out


def main() -> int:
    out = build()
    txt = out.read_text(encoding="utf-8")
    checks = [
        ("외부 스크립트 참조 없음 (파일 하나로 완결)", 'src="./sortable.min.js"' not in txt),
        ("Sortable 인라인됨", "Sortable" in txt),
        ("개인 모드 플래그 주입됨", "window.__PD_SOLO__ = true" in txt),
    ]
    print("=" * 60)
    print("  개인 배포판 빌드")
    print("=" * 60)
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"\n  산출물: {out}")
    print(f"  크기  : {out.stat().st_size:,} bytes")
    if not ok:
        print("\n[FAIL] 검사에 실패했습니다.")
        return 1
    print("\n  이 파일 하나를 팀원에게 전달하면 됩니다 (더블클릭으로 열림).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
