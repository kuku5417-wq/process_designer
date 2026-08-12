"""_netcheck.py — 의존성 설치 **이전에** 사내/사외망을 판정하는 부트스트랩 도구.

setup_env.bat 이 `uv sync` 앞에서 호출해 프록시를 켤지 말지 정한다.
사내망에서 프록시가 꺼져 있으면 pypi 다운로드가 통째로 타임아웃난다
(2026-08-12 사내망 tbm 설치 실패의 직접 원인 — pyodbc 휠 fetch 불가).

**표준 라이브러리만 쓴다.** venv 가 아직 없는 시점에 시스템 파이썬으로 실행되므로
python-dotenv 를 import 할 수 없다. app_config 를 그대로 부르면 dotenv 부재로
.env 가 안 읽히고 NAS_BASE_PATH 가 빈 값이 돼 **항상 사외망으로 오판**한다.
그래서 .env 를 여기서 직접 한 줄 파싱한다.

판정 (둘 중 하나라도 참이면 사내망):
  (a) .env 의 NAS_BASE_PATH 가 실제로 접근 가능      ← app_config.IS_INTERNAL 과 동일 규칙
  (b) .env 의 HTTP_PROXY 호스트:포트에 TCP 연결 성공  ← 프록시를 쓸 수 있는가(설치의 실제 관심사)

(a) 는 앱 런타임 규칙과 일치시키기 위한 것이고, (b) 는 NAS 가 마운트되지 않은
사내망 PC 에서도 설치가 되도록 하는 보완이다.

사용:
    python _netcheck.py            # 종료코드 0=사내망 / 1=사외망 (조용)
    python _netcheck.py --verbose  # 판정 사유를 사람이 읽게 출력
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

DEFAULT_PROXY = "http://60.200.254.1:9090"
TCP_TIMEOUT = 1.5


def find_env_file() -> Path | None:
    """secret/.env 탐색 — app_config._find_env_file 과 같은 규칙(SHI_ENV_FILE → 상위 탐색)."""
    ov = os.getenv("SHI_ENV_FILE")
    if ov and Path(ov).exists():
        return Path(ov)
    here = Path(__file__).resolve()
    for d in (here.parent, *here.parents):
        cand = d / "secret" / ".env"
        if cand.exists():
            return cand
    return None


def read_env(path: Path | None) -> dict[str, str]:
    """.env 를 dotenv 없이 파싱. KEY=VALUE 한 줄 형식만 다룬다(주석·따옴표 제거)."""
    out: dict[str, str] = {}
    if not path:
        return out
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001 — .env 를 못 읽으면 판정 없이 진행(사외망 취급)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


def proxy_reachable(proxy_url: str) -> bool:
    """프록시 host:port 에 TCP 연결이 되는지. URL 파싱도 stdlib 없이 문자열로 처리한다."""
    hostport = proxy_url.split("//", 1)[-1].strip().rstrip("/")
    hostport = hostport.split("/", 1)[0]
    if "@" in hostport:                      # user:pass@host:port 형태 방어
        hostport = hostport.rsplit("@", 1)[-1]
    host, _, port = hostport.partition(":")
    if not host:
        return False
    try:
        with socket.socket() as s:
            s.settimeout(TCP_TIMEOUT)
            return s.connect_ex((host, int(port or 8080))) == 0
    except Exception:  # noqa: BLE001 — DNS 실패·포트 파싱 실패 → 도달 불가로 본다
        return False


def detect() -> tuple[bool, str]:
    """(사내망 여부, 사유) 반환."""
    env_path = find_env_file()
    env = read_env(env_path)
    if not env_path:
        return False, "secret/.env 없음"

    nas = (env.get("NAS_BASE_PATH") or "").strip()
    if nas:
        try:
            if os.path.exists(nas):
                return True, "NAS_BASE_PATH 접근 가능"
        except Exception:  # noqa: BLE001 — UNC 접근이 예외로 끊기는 경우 → 다음 판정으로
            pass

    proxy = (env.get("HTTP_PROXY") or env.get("HTTPS_PROXY") or "").strip() or DEFAULT_PROXY
    if proxy_reachable(proxy):
        return True, "프록시 TCP 도달 가능"

    return False, "NAS 미접근 + 프록시 미도달"


def main() -> int:
    internal, why = detect()
    if "--verbose" in sys.argv[1:]:
        print(f"{'corporate' if internal else 'external'} ({why})")
    return 0 if internal else 1


if __name__ == "__main__":
    raise SystemExit(main())
