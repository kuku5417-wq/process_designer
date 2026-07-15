"""path_config.py — process_designer 경로 관리.

프로세스 계층도 데이터는 중앙 parquet 폴더를 오염시키지 않도록 별도 디렉토리
(<base>/process)에 JSON 으로 저장한다. 이 앱은 parquet 을 생산하지 않는다
(공통규칙 3: parquet 단일 생산자 = data_manager).

data_manager 와 동일한 secret/.env 를 재사용해 NAS(사내망)/로컬(사외망) 자동 전환.
공용PC 로컬 배포여도 이 함수만 경유하면 사내망 이전 시 코드 변경이 없다.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

BASE_DIR   = Path(__file__).resolve().parent      # process_designer/
CODE_DIR   = BASE_DIR.parent                       # code_N/
DATA_LOCAL = CODE_DIR / "data"                     # code_N/data (폴백 base)


def _find_secret_env() -> Path:
    """secret/.env 위치를 포터블하게 탐색 (app_config._find_env_file 과 동일 규칙).

    우선순위: 환경변수 SHI_ENV_FILE → 이 파일 위치에서 위로 올라가며 첫 secret/.env
    → 기본값(CODE_DIR/secret/.env, 없어도 무해).
    """
    ov = os.getenv("SHI_ENV_FILE")
    if ov and Path(ov).exists():
        return Path(ov)
    for d in (BASE_DIR, *BASE_DIR.parents):
        cand = d / "secret" / ".env"
        if cand.exists():
            return cand
    return CODE_DIR / "secret" / ".env"


SECRET_ENV = _find_secret_env()


def read_secret(key: str) -> str:
    """secret/.env 에서 KEY= 값 읽기 (없으면 빈 문자열). 값은 로그·화면에 노출 금지."""
    try:
        if not SECRET_ENV.exists():
            return ""
        text = SECRET_ENV.read_text(encoding="utf-8")
        m = re.search(rf"^{re.escape(key)}[ \t]*=[ \t]*([^\n]*)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


_ensured: set[Path] = set()


def _ensure(d: Path) -> Path:
    """디렉토리 생성 (프로세스당 1회만 시도 — 리런마다 mkdir syscall 방지)."""
    if d not in _ensured:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _ensured.add(d)
    return d


@lru_cache(maxsize=1)
def _nas_base() -> Path | None:
    """NAS base 경로 (사내망이고 실제 접근 가능할 때만)."""
    nas = read_secret("NAS_BASE_PATH") or read_secret("NAS_PATH")
    if not nas:
        return None
    p = Path(nas)
    try:
        if p.exists():
            return p
    except Exception:
        pass
    return None


def is_internal() -> bool:
    return _nas_base() is not None


def _local_base() -> Path:
    base = read_secret("DATA_PATH")
    return Path(base) if base else DATA_LOCAL


def get_process_dir() -> Path:
    """프로세스 설계 데이터 저장 디렉토리 (읽기/쓰기).

    우선순위: .env PROCESS_DATA_PATH → <NAS or DATA_PATH>/process → code_N/data/process.
    """
    ov = read_secret("PROCESS_DATA_PATH") or os.getenv("PROCESS_DATA_PATH", "")
    if ov:
        return _ensure(Path(ov))
    nas = _nas_base()
    root = nas if nas else _local_base()
    return _ensure(root / "process")


def tree_path() -> Path:
    """프로세스 계층도 정본 파일."""
    return get_process_dir() / "process_tree.json"


def get_history_dir() -> Path:
    """스냅샷 이력 디렉토리."""
    return _ensure(get_process_dir() / "history")


def audit_path() -> Path:
    """저장 감사로그 (append-only jsonl)."""
    return get_history_dir() / "_audit.jsonl"


def get_env_label() -> str:
    """현재 환경 레이블 (UI 표시용)."""
    nas = _nas_base()
    if nas:
        return f"🟢 사내망 (NAS: {nas})"
    return f"🟡 사외망 (로컬: {get_process_dir()})"


def invalidate_cache() -> None:
    """경로 캐시 무효화 (NAS 재연결 시 사용)."""
    _nas_base.cache_clear()
    _ensured.clear()
