"""app_config.py — 전 앱(data_manager·tbm·tbm_mssql·esg·esg_mssql·OCR_N) 공용 환경설정.

★ 전 repo 동일본 유지 원칙 — 이 파일을 수정하면 6개 repo 전체에 동일하게 배포할 것.

단일 소스: 상위 폴더의 secret/.env (python-dotenv 로드, 상위 탐색·SHI_ENV_FILE 지정 가능).
사내/사외망은 NAS_BASE_PATH 접근 가능 여부로 자동 감지.

※ 파일명이 config.py가 아니라 app_config.py 인 이유: esg/tbm에 이미 `config/` 패키지가
  있어 `import config`가 충돌한다. 각 앱은 sys.path에 repo 폴더를 추가해 `import app_config`.

키 이름은 실제 .env 키와 1:1 (NAS_BASE_PATH / DATA_PATH / DB_MYSQL_* / LLM_SOLAR_* /
LLM_UPSTAGE_* / OCR_* / HTTP_PROXY·HTTPS_PROXY / WEATHER_*).

프록시 정책(전 앱 공통): 사내망(IS_INTERNAL) 또는 VDI 에서 프록시 사용 — 외부 인터넷
호출용. 사내 서버(sola/DoXA 등) 호출부는 proxies={"http":None,"https":None} 을 명시해
프록시를 우회할 것 (내부 IP가 외부 프록시에 막혀 403 차단되는 문제 방지).
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_env_file() -> str:
    """secret/.env 위치를 포터블하게 탐색.

    우선순위: 환경변수 SHI_ENV_FILE → app_config.py 위치에서 위로 올라가며 첫 secret/.env.
    (사내망에서 임의 경로에 압축해제해도 동작 — F:\\code 하드코드 제거.)
    """
    ov = os.getenv("SHI_ENV_FILE")
    if ov and Path(ov).exists():
        return ov
    here = Path(__file__).resolve()
    for d in (here.parent, *here.parents):
        cand = d / "secret" / ".env"
        if cand.exists():
            return str(cand)
    return str(here.parent / "secret" / ".env")   # 기본값(없어도 무해)


# .env 로드 (python-dotenv). 미설치 환경 대비 안전 처리.
try:
    from dotenv import load_dotenv
    load_dotenv(_find_env_file())
except Exception:
    pass


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── 망 감지 ─────────────────────────────────────────────
NAS_BASE_PATH = _get("NAS_BASE_PATH")
IS_INTERNAL   = bool(NAS_BASE_PATH) and os.path.exists(NAS_BASE_PATH)
ENV_LABEL     = "사내망" if IS_INTERNAL else "사외망"

# ── VDI(가상 데스크톱) 환경 ──────────────────────────────
# 사외망이라도 인터넷이 회사 프록시 경유로만 나가는 VDI는 .env에 VDI=true 설정.
#   · 사외망 + VDI    → 프록시 + SSL검사(verify=False) 사용
#   · 사외망 + 비VDI  → 직접연결(프록시 미사용)  ← 일반 사외망 PC 기존 동작
IS_VDI = _get("VDI", "").strip().lower() in ("true", "1", "yes", "y")

# ── 경로 (사외망 DATA_PATH 미설정 시 기본값으로 크래시 방지) ──
DATA_PATH = _get("DATA_PATH") or r"F:\code\data"
if IS_INTERNAL:
    BASE_PATH    = NAS_BASE_PATH
    UPLOAD_PATH  = _get("NAS_UPLOAD_PATH")  or os.path.join(BASE_PATH, "upload")
    PARQUET_PATH = _get("NAS_PARQUET_PATH") or os.path.join(BASE_PATH, "parquet")
    DUMMY_PATH   = _get("NAS_DUMMY_PATH")   or os.path.join(BASE_PATH, "dummy")
else:
    BASE_PATH    = DATA_PATH
    UPLOAD_PATH  = os.path.join(BASE_PATH, "upload")
    PARQUET_PATH = os.path.join(BASE_PATH, "parquet")
    DUMMY_PATH   = os.path.join(BASE_PATH, "dummy")

# ── MySQL ───────────────────────────────────────────────
DB_MYSQL_HOST     = _get("DB_MYSQL_HOST")
DB_MYSQL_PORT     = _get("DB_MYSQL_PORT", "3306")
DB_MYSQL_DATABASE = _get("DB_MYSQL_DATABASE")
DB_MYSQL_USER     = _get("DB_MYSQL_USER")
DB_MYSQL_PASSWORD = _get("DB_MYSQL_PASSWORD")
USE_PARQUET = (not IS_INTERNAL) or (not DB_MYSQL_HOST) or DB_MYSQL_HOST.startswith("YOUR_")


def db_url() -> str | None:
    """SQLAlchemy URL. 폴백 조건(사외망/HOST 미설정)이면 None."""
    if USE_PARQUET or not DB_MYSQL_HOST:
        return None
    return (f"mysql+pymysql://{DB_MYSQL_USER}:{DB_MYSQL_PASSWORD}"
            f"@{DB_MYSQL_HOST}:{DB_MYSQL_PORT}/{DB_MYSQL_DATABASE}?charset=utf8mb4")


# ── LLM (raw 키 + 망별 자동전환) ────────────────────────
LLM_SOLAR_API_KEY    = _get("LLM_SOLAR_API_KEY")
LLM_SOLAR_API_URL    = _get("LLM_SOLAR_API_URL")
LLM_SOLAR_MODEL      = _get("LLM_SOLAR_MODEL")   # OpenAI 호환 model명(예: solar-1-mini-chat). 비면 생략
LLM_UPSTAGE_API_KEY  = _get("LLM_UPSTAGE_API_KEY")
LLM_UPSTAGE_BASE_URL = _get("LLM_UPSTAGE_BASE_URL", "https://api.upstage.ai/v1")
LLM_UPSTAGE_MODEL    = _get("LLM_UPSTAGE_MODEL", "solar-pro")
# OpenAI 폴백 (실 .env엔 없을 수 있음 → 빈 값이면 폴백 비활성)
LLM_OPENAI_API_KEY   = _get("LLM_OPENAI_API_KEY")
LLM_OPENAI_MODEL     = _get("LLM_OPENAI_MODEL", "gpt-4o-mini")
if IS_INTERNAL:
    LLM_API_KEY, LLM_API_URL = LLM_SOLAR_API_KEY, LLM_SOLAR_API_URL
else:
    LLM_API_KEY, LLM_API_URL = LLM_UPSTAGE_API_KEY, LLM_UPSTAGE_BASE_URL

# ── OCR (raw 키 + 망별 자동전환) ────────────────────────
OCR_SOLAR_API_KEY   = _get("OCR_SOLAR_API_KEY")
OCR_SOLAR_API_URL   = _get("OCR_SOLAR_API_URL")
OCR_UPSTAGE_API_KEY = _get("OCR_UPSTAGE_API_KEY")
OCR_UPSTAGE_API_URL = _get("OCR_UPSTAGE_API_URL")
OCR_DOXA_API_KEY    = _get("OCR_DOXA_API_KEY")
OCR_DOXA_API_URL    = _get("OCR_DOXA_API_URL")
# OCR 모델: 사내망 sola는 OpenAI SDK(vision) 모델명 필요, 사외 Upstage는 multipart의 model="ocr"
OCR_SOLAR_MODEL     = _get("OCR_SOLAR_MODEL")
OCR_UPSTAGE_MODEL   = _get("OCR_UPSTAGE_MODEL", "ocr")
if IS_INTERNAL:
    OCR_API_KEY, OCR_API_URL, OCR_MODEL = OCR_SOLAR_API_KEY, OCR_SOLAR_API_URL, OCR_SOLAR_MODEL
else:
    OCR_API_KEY, OCR_API_URL, OCR_MODEL = OCR_UPSTAGE_API_KEY, OCR_UPSTAGE_API_URL, OCR_UPSTAGE_MODEL

# ── 프록시 / SSL (사내망 + VDI 사외망은 프록시 사용, 일반 사외망은 직접연결) ──
if IS_INTERNAL or IS_VDI:
    HTTP_PROXY  = _get("HTTP_PROXY")  or None
    HTTPS_PROXY = _get("HTTPS_PROXY") or None
else:
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(_k, None)
    HTTP_PROXY = HTTPS_PROXY = None
USE_PROXY  = bool(HTTP_PROXY or HTTPS_PROXY)
SSL_VERIFY = not USE_PROXY


def proxies() -> dict | None:
    """requests용 proxies dict. 비활성 시 None."""
    if not USE_PROXY:
        return None
    return {"http": HTTP_PROXY or HTTPS_PROXY, "https": HTTPS_PROXY or HTTP_PROXY}


# ── 기상청 ──────────────────────────────────────────────
WEATHER_API_KEY  = _get("WEATHER_API_KEY")
# 기상청 API허브(authKey) 기준. data.go.kr(serviceKey) 쓰려면 .env에서 WEATHER_BASE_URL 재설정.
WEATHER_BASE_URL = _get("WEATHER_BASE_URL",
                        "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0")


if __name__ == "__main__":
    print(f"환경        : {ENV_LABEL}  (VDI={IS_VDI})")
    print(f"BASE_PATH   : {BASE_PATH}")
    print(f"PARQUET_PATH: {PARQUET_PATH}")
    print(f"UPLOAD_PATH : {UPLOAD_PATH}")
    print(f"USE_PARQUET : {USE_PARQUET}")
    print(f"USE_PROXY   : {USE_PROXY}")
    print(f"db_url set  : {bool(db_url())}")
    print(f"LLM_API_URL : {LLM_API_URL}")
    print(f"OCR_API_URL : {OCR_API_URL}")
