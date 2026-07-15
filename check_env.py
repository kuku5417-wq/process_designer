"""check_env.py — 가동 전 환경 점검 (process_designer).

사용: python check_env.py        (전체)
      python check_env.py --quick (쓰기 테스트 생략)

출력 규약(형제 앱 공통): [PASS]/[WARN]/[FAIL]/[INFO], FAIL 이 있으면 종료코드 1.
시크릿 값은 절대 출력하지 않는다 — "설정됨/미설정" 만.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_fail = 0
_warn = 0


def ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def warn(msg: str) -> None:
    global _warn
    _warn += 1
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    global _fail
    _fail += 1
    print(f"[FAIL] {msg}")


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def main() -> int:
    quick = "--quick" in sys.argv
    print("=" * 60)
    print("  process_designer 환경 점검")
    print("=" * 60)

    # 1. 파이썬
    v = sys.version_info
    bits = 64 if sys.maxsize > 2**32 else 32
    if v.major == 3 and v.minor == 12 and bits == 64:
        ok(f"Python {v.major}.{v.minor}.{v.micro} ({bits}bit)")
    elif bits == 32:
        fail(f"32비트 Python 감지 ({v.major}.{v.minor}) — pandas/pyarrow 휠이 없습니다. 64비트 3.12 를 설치하세요.")
    else:
        warn(f"Python {v.major}.{v.minor} ({bits}bit) — 권장은 3.12 64bit")

    # 2. 의존 패키지
    for mod, why in [("streamlit", "앱 실행"), ("pandas", "엑셀/표"),
                     ("openpyxl", "엑셀 읽기/쓰기"), ("dotenv", ".env 로드")]:
        try:
            m = __import__(mod)
            ok(f"{mod} {getattr(m, '__version__', '')}".strip() + f"  ({why})")
        except Exception as e:
            fail(f"{mod} 미설치 ({why}) — uv sync 를 실행하세요. {e}")

    # pyarrow: parquet 을 쓰지 않지만 커스텀 컴포넌트가 import 한다
    try:
        import pyarrow
        ok(f"pyarrow {pyarrow.__version__}  (드래그앤드롭 컴포넌트 필수)")
    except Exception:
        fail("pyarrow 미설치 — Streamlit 커스텀 컴포넌트가 pyarrow 를 import 하므로 "
             "드래그앤드롭 보드가 뜨지 않습니다 (간단 모드로는 동작).")

    # 3. 프론트엔드 자산
    fe = Path(__file__).resolve().parent / "frontend"
    for f, why in [("index.html", "보드 화면"), ("sortable.min.js", "드래그 엔진")]:
        p = fe / f
        if p.exists() and p.stat().st_size > 0:
            ok(f"frontend/{f}  ({p.stat().st_size:,} bytes, {why})")
        else:
            warn(f"frontend/{f} 없음 — 드래그앤드롭 대신 간단 모드로 동작합니다. ({why})")

    # 4. 경로 / 데이터
    try:
        import path_config as pc
        info(pc.get_env_label())
        d = pc.get_process_dir()
        if d.exists():
            ok(f"데이터 폴더: {d}")
        else:
            fail(f"데이터 폴더를 만들 수 없습니다: {d}")
        t = pc.tree_path()
        if t.exists():
            ok(f"데이터 파일 있음: {t.name} ({t.stat().st_size:,} bytes)")
        else:
            info(f"데이터 파일 없음 — 첫 실행 시 기본 계층으로 시작합니다. ({t.name})")
        info(f"secret/.env: {'설정됨' if pc.SECRET_ENV.exists() else '미설정 (사외망 로컬 경로로 동작)'}")
    except Exception as e:
        fail(f"path_config 로드 실패: {e}")
        return 1

    # 5. 데이터 무결성
    try:
        import store
        data, warns = store.load_tree()
        import schema
        errs = schema.validate(data)
        if errs:
            warn(f"데이터 구조 경고 {len(errs)}건: {errs[0]}")
        else:
            ok(f"데이터 정상 — 업무 {len(data['nodes'])}개, rev {data.get('rev', 0)}")
        for w in warns:
            warn(w)
    except Exception as e:
        fail(f"데이터 로드 실패: {e}")

    # 6. 쓰기 권한
    if quick:
        info("--quick: 쓰기 테스트 생략")
    else:
        try:
            probe = pc.get_process_dir() / ".write_probe.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            ok("데이터 폴더 쓰기 권한 정상")
        except Exception as e:
            fail(f"데이터 폴더에 쓸 수 없습니다 — 저장이 불가능합니다: {e}")

    print("-" * 60)
    if _fail:
        print(f"[FAIL] {_fail}건 / [WARN] {_warn}건 — 위 [FAIL] 항목을 해결하세요.")
        return 1
    print(f"[OK] 점검 통과 (경고 {_warn}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
