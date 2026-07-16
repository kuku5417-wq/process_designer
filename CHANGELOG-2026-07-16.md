# process_designer 변경 이력 — 2026-07-16

사내망 공용PC 에서 `setup_env.bat` 이 **pyarrow 다운로드 실패**로 죽던 문제를 고쳤다.
원인은 setup_env.bat 이 아니라 **uv.lock 의 의존성 버전**이었다. 의존성 15종을 `data_manager` 와
동일 버전으로 고정하고, 네트워크를 완전히 끊은 상태(`uv sync --locked --offline`)로 사내망을
재현해 **48개 패키지 전부 설치 성공**을 확인했다.

> 이 문서는 `dddfa88` 한 건만 다룬다. 같은 날짜의 `885c230`·`00f0194`·`178f4af` 는
> **병렬 세션의 UI 작업**이며 이 문서의 범위가 아니다.

---

## 기능별 요약

| 구분 | 내용 |
|---|---|
| (Fix) 사내망 설치 실패 | 의존성 15종이 형제 앱과 어긋나 **이 앱만 pypi 에서 40MB+ 를 새로 받아야 했다.** 사내망은 pypi 직접 접속이 막혀 있어 실패 |
| 버전 정렬 | `[tool.uv] constraint-dependencies` 로 15종을 `data_manager` 와 동일 버전 고정 (pyarrow 25.0.0 → **24.0.0** 등) |
| 재발 방지 | `uv sync --locked` — lock 이 어긋나면 **조용히 최신으로 re-resolve** 하던 것을 차단. 이번 버그가 생긴 경로 그 자체다 |
| 사내망 프록시 | `choice` 프롬프트로 사내망/사외망 선택. 10초 무응답 시 사외망(= 기존 동작, 회귀 없음) |
| (Fix) 거짓 진단 | `[FAIL]` 블록이 실패 원인과 무관하게 힌트 4개를 **항상 전부 출력**해 오진을 유발했다 |

---

## 상세 변경

### `pyproject.toml`

`[tool.uv]` 에 `constraint-dependencies` 추가. `dependencies` 의 기존 하한(`pyarrow>=14.0.0` 등)은
의미가 문서화돼 있어 **그대로 두고**, 상한만 별도로 강제한다. `constraint-dependencies` 는
**전이 의존성에도 걸리므로** streamlit 이 끌고 오는 narwhals·uvicorn 까지 잡힌다.

| 패키지 | 변경 | | 패키지 | 변경 |
|---|---|---|---|---|
| pyarrow | 25.0.0 → **24.0.0** | | gitpython | 3.1.51 → 3.1.50 |
| numpy | 2.5.1 → **2.4.6** | | pydeck | 0.9.3 → 0.9.2 |
| streamlit | 1.59.1 → **1.58.0** | | websockets | 16.1 → 16.0 |
| pillow | 12.3.0 → 12.2.0 | | anyio | 4.14.2 → 4.13.0 |
| rpds-py | 2026.6.3 → 2026.5.1 | | charset-normalizer | 3.4.9 → 3.4.7 |
| narwhals | 2.24.0 → 2.22.0 | | uvicorn | 0.51.0 → 0.49.0 |
| click | 8.4.2 → 8.4.1 | | typing-extensions | 4.16.0 → 4.15.0 |
| tzdata | 2026.3 → 2026.2 | | | |

### `setup_env.bat`

- **네트워크 선택** — REM 처리돼 있던 프록시 2줄을 `choice /C OI /T 10 /D O` 프롬프트로 교체.
  `I` 선택 시 `HTTP_PROXY`/`HTTPS_PROXY` 설정. 괄호 블록 안 지연확장 문제를 피하려고
  `HTTPS_PROXY` 는 `%HTTP_PROXY%` 참조 대신 값을 직접 쓴다. ASCII + CRLF 유지(공통지침).
- **`uv sync --locked`** — uv.lock 을 그대로 쓰고, 어긋나면 시끄럽게 실패시킨다.
- **`[FAIL]` 문구 정정** — "힌트일 뿐 탐지 결과가 아님"을 명시. **32비트 힌트는 삭제.**

### `uv.lock`

재생성(48 패키지). 15종 다운그레이드.

---

## 설계 결정 (왜)

- **setup_env.bat 은 범인이 아니었다** — 8개 앱의 setup_env.bat 을 전부 비교했더니 프록시·TLS·
  UV_* 블록이 **바이트 단위로 동일**했다(유일한 차이는 주석 한 줄과 requirements.txt 폴백 유무).
  `[tool.uv]`·`.python-version` 도 costplan 과 같은 최신 패턴이라 오히려 정상이었다.
  **"다른 앱은 되는데 이건 안 된다"의 답은 스크립트가 아니라 lock 에 있었다.**

- **왜 이 앱만 실패했나** — 가장 늦게(7/15) 만들어져 의존성이 `>=` 하한만 있는 상태로 처음부터
  resolve 됐고, 전 패키지가 "그 시점 최신"으로 잡혔다. 형제 앱들의 lock 은 그 전에 만들어졌고
  `uv lock` 은 보수적이라 기존 핀을 유지한다. 그 결과 48개 중 **15개가 다른 어떤 앱도 안 쓰는 버전**.
  사내망은 pypi 가 막혀 있어 **uv 캐시에 없는 휠은 못 받는다.** 다른 앱들은 필요한 휠이 이미
  캐시에 있어 네트워크 없이 끝나고, 이 앱만 pyarrow(~25MB)·numpy 등을 새로 받아야 했다.

- **기준을 "형제 앱 다수결"이 아니라 `data_manager` 로 잡았다** — 처음엔 다수결(narwhals 2.22.1,
  anyio 4.14.0)로 맞췄는데 오프라인 테스트가 narwhals 에서 실패했다.
  **uv 캐시는 lock 파일이 아니라 그 PC에 실제로 설치된 것만 갖고 있기 때문이다.**
  이 개발PC 에는 data_manager 만 깔려 있어 2.22.0/4.13.0 만 캐시에 있었다.
  `data_manager` 는 전 앱의 parquet 생산자라 **어느 PC에나 깔려 있을 가능성이 가장 높다** — 그래서
  기준으로 삼았다. **버전을 올릴 땐 data_manager 와 함께 올려야 캐시 공유가 유지된다.**

- **프록시는 자동감지하지 않는다** — ping 은 방화벽에 막히고, NAS 경로 판별은 `.env` 를 읽어야 해서
  ASCII 배치 파일에서 오판 위험이 크다. 이미 파일 안에 `choice` 전례가 있고, 10초 기본값을
  사외망(현 운영 환경)으로 둬 기존 동작과 같게 했다.

- **32비트 힌트를 지운 이유 — 항상 거짓이기 때문이다** — `uv python find cpython-3.12-windows-x86_64`
  가드가 `uv sync` **보다 먼저** 돌고, 실패하면 다른 메시지를 찍고 `goto :end` 로 빠진다.
  즉 **`[FAIL]` 블록에 도달했다는 것 자체가 64비트 검사를 통과했다는 뜻**이다.
  사용자가 본 "SSL unknown / 32bit python detected" 는 **탐지가 아니라 고정 echo 문자열**이었고,
  실제 원인(pyarrow 다운로드 실패) 하나를 3개처럼 보이게 만들었다.

- **`--locked` 를 넣은 이유** — 기존엔 lock 이 pyproject 와 어긋나면 uv 가 조용히 최신으로
  re-resolve 했다. 이번 버그가 생긴 경로가 정확히 그것이라, 같은 일이 다시 일어나지 않게
  막고 공용PC 설치를 결정론적으로 만들었다. (7개 형제 앱 어디에도 `--locked`/`--frozen` 이 없다.)

---

## 검증 결과

| 항목 | 결과 |
|---|---|
| **사내망 재현** | `uv sync --locked --offline` — 네트워크 완전 차단 상태에서 **48개 전부 설치 성공**. 수정 전엔 narwhals/pyarrow 에서 실패 |
| 캐시 적중 | 형제 앱과 버전이 어긋난 패키지 **15개 → 0개** (남은 1건은 로컬 프로젝트 자신 `source = { virtual = "." }` — 다운로드 대상 아님) |
| `_smoke.py` | **70/70 PASS** |
| `check_env.py` | 통과, 경고 0건. pyarrow 24.0.0 · streamlit 1.58.0 · Python 3.12.7 (64bit) |
| 커스텀 컴포넌트 | streamlit 1.58.0 에서 `declare_component` / `create_instance(args, default, key, on_change, tab_index, kwargs)` 정상, pyarrow import 정상. app.py 호출부(`key`·`default`)와 시그니처 일치 |
| bat 분기 | `I` → `proxy ON` + `HTTP_PROXY` 설정 / `O` → `proxy OFF` + 빈값. errorlevel 분기 정상 |
| bat 인코딩 | ASCII 100% · CRLF 96/96 (bare-LF 0) — 공통지침 유지 |

---

## 알려진 사실 / 미해결

- **브라우저 DnD 실동작 미확인** — streamlit 1.58.0 다운그레이드의 컴포넌트 계약은 API 수준에서
  검증했으나, **화면 확인은 못 했다.** 병렬 세션이 8540 포트를 점유 중이고 `app.py`·`schema.py`·
  `frontend/index.html` 이 그쪽에서 수정 중이라, 지금 띄우면 이 변경과 무관한 실패가 난다.
  CLAUDE.md 규칙대로 **브라우저 확인이 별도로 필요하다.**
- **8540 서버 재시작 필요** — venv 를 다운그레이드했는데 병렬 세션의 서버는 메모리에 streamlit
  1.59.1 을 들고 있다. 디스크의 모듈이 1.58.0 으로 교체돼 **불일치 상태**다.
- **`constraint-dependencies` 는 수동 동기화다** — data_manager 가 버전을 올리면 이 목록도 같이
  올려야 한다. 안 올리면 캐시가 갈라져 같은 문제가 재발한다. 자동화 장치는 없다.
- **같은 원인이 `log`(SSIMS, 8000)에 잠재** — lock 이 7/15 로 최신이라 사내망 신규 설치 시 동일
  증상 가능. 다만 pyarrow 는 24.0.0 이라 위험도는 낮다. (별건)
- **루트 `CLAUDE.md` 프로젝트 지도에 process_designer(8540) 가 빠져 있다** — "9개 프로젝트"라고
  적혀 있으나 표에는 8개뿐이다. (별건)

---

관련 커밋: `dddfa88`(사내망 설치 실패 수정).
