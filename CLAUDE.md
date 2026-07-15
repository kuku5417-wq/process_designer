# process_designer

시운전팀 **프로세스 설계 앱**. lv0 조선 › lv1 생산 › lv2 시운전(고정) 아래로 lv3~lv6 업무
계층을 카드로 그리고, 업무별 **AI 에이전트 적용 여부·활용 기술·담당 부서**를 기록한다.
공용PC에 띄워 여러 명이 함께 편집한다.

## 실행

```bash
uv run streamlit run app.py --server.port 8540
```
- 최초 셋업: `setup_env.bat` (venv → check_env → 실행) / 이후: `run.bat`
- 가동 전 점검: `python check_env.py` (`--quick` = 쓰기 테스트 생략)
- 회귀검증: `python _smoke.py` — Streamlit 없이 schema/store/excel 43항목 검증. **로직 수정 후 반드시 실행.**

## 파일 구조

| 파일 | 역할 |
|---|---|
| `app.py` | 진입점 — 사이드바(작성자·저장·메뉴) + 라우터 + 충돌/재읽기 다이얼로그 |
| `schema.py` | **데이터 모델 정본** — 상수·노드 생성·트리 조작·정규화·검증. Streamlit 의존 없는 순수 함수 |
| `store.py` | JSON 원자적 저장 · 스냅샷 이력 · rev 충돌 검사 · 감사로그 |
| `state.py` | session_state 헬퍼 (트리·선택·변경 카운터) |
| `excel_io.py` | 계층 ↔ 엑셀 (다운로드 3시트 / 업로드 파싱) |
| `dnd_component.py` | 커스텀 컴포넌트 래퍼 + 가용성 판정 + 이벤트 중복 가드 |
| `frontend/index.html` | 컬럼 드릴다운 DnD 보드 (postMessage 프로토콜 직접 구현) |
| `frontend/sortable.min.js` | SortableJS 1.15.6 (MIT) — **런타임 CDN 의존 없음, repo 에 커밋됨** |
| `views/tree_view.py` | 계층 편집 (DnD 경로 + 간단 모드 + 카드 상세) |
| `views/domain_view.py` | 도메인 마스터 CRUD |
| `views/excel_view.py` | 엑셀 다운로드/업로드 + 요약 |
| `views/history_view.py` | 스냅샷 이력 · 복원 · 저장 기록 |
| `path_config.py` | 데이터 경로 (NAS/로컬 자동 전환) |
| `app_config.py` | 전 repo 동일본 (수정 시 7개 repo 동기화) |
| `pii.py` | `mask_name`/`mask_phone` (tbm/modules/pii.py 이식) |

## 데이터

`path_config.get_process_dir()` = `.env PROCESS_DATA_PATH` → `<NAS or DATA_PATH>/process` → `code_N/data/process`

```
<base>/process/
├── process_tree.json     정본 (nodes + domains 한 파일)
└── history/
    ├── process_tree_YYYYMMDD_HHMMSS_작성자.json   덮어쓰기 직전 스냅샷(pre-image)
    └── _audit.jsonl                                저장 기록 (append-only)
```

**parquet 을 생산하지 않는다** (공통규칙 3). 단 `pyarrow` 는 의존성에 필요하다 — Streamlit 의
`components.v1.custom_component.create_instance` 가 pyarrow 를 import 하므로, 없으면 DnD 보드가
`StreamlitAPIException` 으로 죽는다. 지우지 말 것.

## 핵심 규칙

- **평면 노드 배열 + `parent_id`** (중첩 JSON 아님). 이동 = `parent_id`/`order` 2필드 수정.
  사이클은 `would_cycle()` 로 차단, `normalize()` 가 로드 시 `level` 을 깊이로 재계산한다.
- **lv0~lv2 는 노드가 아니다.** `schema.FIXED_LEVELS` 상수이고 lv3 의 `parent_id` 는 `ROOT_ID("__root__")`.
  엑셀 내보내기 시점에만 lv0~lv2 컬럼으로 재부착한다.
- **도메인 마스터는 `process_tree.json` 안에** (`domains` 키). 노드가 참조하는 값이라 항상 함께
  원자적으로 저장돼야 한다 — 파일을 쪼개면 "기술명 rename + 노드 일괄 치환"이 2회 write 로 갈라진다.
- **충돌 검사는 `rev`(단조 증가 정수)가 정본.** mtime 은 NAS 해상도·시계 스큐 때문에 신뢰할 수 없어
  "누가 방금 저장했다" 배너 감지용으로만 쓴다. 자동 병합은 하지 않는다.
- **스냅샷은 pre-image** — 강제 덮어쓰기를 해도 상대 버전이 이력에 남아 복원 가능하다.
- **저장은 [저장] 버튼에서만.** 편집은 세션 메모리에만 반영한다(드래그마다 저장하면 스냅샷 폭발).
- **PII**: 카드·표·엑셀은 `mask_name` 마스킹, **상세 편집 입력 위젯만 원본**(마스킹하면 편집 불가).
  DnD 카드 payload 에는 마스킹된 이름만 실어 **원본이 iframe DOM 에 존재하지 않게** 한다.
- **폴백 필수**: `frontend/` 자산이나 pyarrow 가 없으면 자동으로 간단 모드(버튼 UI)로 내려간다.
  두 경로는 `tree_view.apply_event` / `schema.*` 를 **공유**한다 — 한쪽만 고치지 말 것.

## 커스텀 컴포넌트 주의 (frontend/index.html)

npm 빌드 없는 정적 컴포넌트다. 아래는 Streamlit 번들에서 확인한 계약이라 어기면 조용히 죽는다.

- 모든 postMessage 에 **`isStreamlitMessage: true`** 필수 — 없으면 부모가 무시하고 60초 뒤 타임아웃.
  그래서 `send()` 한 곳에서만 postMessage 한다.
- 순서: `componentReady({apiVersion:1})` → 부모가 `streamlit:render` 송신 → `setFrameHeight` →
  (사용자 조작) `setComponentValue`. **ready 이전 메시지는 폐기된다.**
- **`render()` 안에서 `setComponentValue` 를 부르면 무한 리런**이다. 사용자 조작 콜백에서만 호출.
- iframe 은 `scrolling:"no"` — 내부 스크롤 불가. 컬럼별 `overflow-y:auto` + 정확한 `setFrameHeight` 필수.
- 되돌려주는 값은 **이벤트 1건**(전체 트리 아님). 보이는 4컬럼만 담긴 부분 상태로 전체 트리를
  덮어쓰면 데이터가 날아간다. `reorder` 는 목적지 리스트 **전체 id 배열**을 동봉해 DOM/서버 drift 를 막는다.
- 중복 처리 가드는 **`evt_id`(UUID)**. 단조 증가 seq 를 쓰면 iframe 새로고침 시 카운터가 1로 리셋돼
  이후 모든 이벤트가 영영 무시된다.
- Sortable 은 **`forceFallback: true`** (네이티브 HTML5 DnD 대신 pointer 이벤트). iframe 안에서
  동작이 일관되고 드래그 중 자동 스크롤을 얻는다. 이 때문에 `dragenter`/`dragleave` 는 발생하지 않아
  드롭존 강조는 `body.dragging .drop:hover` CSS 로 처리한다.
- group 이름을 레벨별(`L3`~`L6`)로 분리해 **lv4 카드가 lv6 컬럼에 떨어지는 사고를 구조적으로 차단**한다.
  레벨 변경(승격/강등)은 DnD 로 불가능하며 상세 패널 `옮기기` selectbox 에서만 가능하다.

## UI

- 텍스트는 한국어. 사이드바: 브랜드 → 환경 배지 → 작성자 → 저장/변경 배지 → 디스크 다시 읽기 →
  메뉴 4종 → DnD 토글.
- 보드: `lv3 │ lv4 │ lv5 │ lv6` 컬럼 드릴다운. 카드 클릭=선택, `＋`=추가, 카드 위 드롭=부모 변경.
