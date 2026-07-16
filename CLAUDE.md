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
- 회귀검증: `python _smoke.py` — Streamlit 없이 schema/store/excel 61항목 검증. **로직 수정 후 반드시 실행.**
  (프론트엔드 JS 는 `_smoke.py` 범위 밖 — 브라우저에서 직접 확인해야 한다.)

## 파일 구조

**구조 (v2)**: `frontend/index.html` **한 파일이 전체 UI 와 편집 상태**를 브라우저에서 들고 있고,
`app.py` 는 "데이터 저장 API" 역할만 한다. 컴포넌트가 [저장] 시 트리 전체를 되돌려주면
`store.save_tree()` 로 원자적 저장 + 스냅샷 + rev 충돌검사를 한다. Streamlit 위젯은 쓰지 않는다.

| 파일 | 역할 |
|---|---|
| `app.py` | 진입점 겸 저장 API — 컴포넌트 호스팅, 이벤트(save/force/download/import/restore/reload) 처리 |
| `frontend/index.html` | **UI 전체** — 탭 4종·컬럼/전체보기 보드·상세폼·도메인·엑셀·이력 + DnD + postMessage |
| `frontend/sortable.min.js` | SortableJS 1.15.6 (MIT) — **런타임 CDN 의존 없음, repo 에 커밋됨** |
| `schema.py` | **데이터 모델 정본** — 상수·노드 생성·트리 조작·정규화·검증. Streamlit 의존 없는 순수 함수 |
| `store.py` | JSON 원자적 저장 · 스냅샷 이력 · rev 충돌 검사 · 감사로그 |
| `excel_io.py` | 계층 ↔ 엑셀 (다운로드 3시트 / 업로드 파싱) |
| `path_config.py` | 데이터 경로 (NAS/로컬 자동 전환) |
| `app_config.py` | 전 repo 동일본 (수정 시 7개 repo 동기화) |
| `pii.py` | `mask_name`/`mask_phone` — 엑셀 출력용. **화면 마스킹은 index.html 의 `maskName()`** |

> **v1 잔존 파일** — `state.py` / `ui_styles.py` / `dnd_component.py` / `views/` 는 v2 `app.py` 가
> 더 이상 import 하지 않는다(죽은 코드). 정리 전까지는 **수정해도 앱에 반영되지 않는다**.

### 프론트엔드 규칙 (index.html)

- **JS 로직은 파이썬과 쌍둥이다.** `hasDetail`/`maskName`/`actMove`/`actReorder` 는 각각
  `schema.has_detail` / `pii.mask_name` / `apply_move` / `apply_reorder` 와 **동일 규칙**이어야 한다.
  한쪽만 고치면 화면과 엑셀·저장 결과가 어긋난다.
- **드래그 콜백(onEnd/onAdd)에서 곧바로 `rerender()` 하지 말 것.** `rerender` → `wireDnD` 가
  드래그 중인 Sortable 을 destroy 해 `onEnd` 가 영영 오지 않고, `dragging` 클래스와 `_dragging`
  가드가 남아 **앱 전체가 클릭 불능**이 된다. 반드시 `setTimeout(...,0)` 으로 다음 틱에 미룬다.
- DnD group 은 레벨별(`L3`~`L6`)로 분리 — 같은 레벨끼리만 오가므로 레벨 변경·사이클이 원천 불가능하다.
- `seed()` / `MOCK_HIST` 는 `!IN_ST`(Streamlit 밖 단독 미리보기)에서만 쓰는 디자인용 더미다.
  실제 데이터는 항상 `args.tree` 로 들어온다 — 이 분기를 흐리지 말 것.

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

- **상세 필드는 lv6 세부업무만.** lv3~lv5 는 업무를 묶는 분류 그룹이라 **이름 + 설명**만 받는다.
  규칙 정본은 `schema.has_detail(level)` — 화면(`_full_form`/`_group_form`), 카드 칩
  (`build_columns`), 집계(`stats`)가 모두 이 함수 하나를 본다. **한 곳만 고치면 안 된다.**
- **레벨이 바뀌어도 상세 값은 지우지 않는다** — 화면에서 숨길 뿐이다. lv6→lv5 로 승격했다가
  되돌리면 값이 그대로 살아난다(실수 복구). `normalize()` 가 상위 레벨 필드를 비우게 만들지 말 것.
  숨은 값은 `schema.has_hidden_detail(node)` 로 감지해 상세 패널에 안내하고, 엑셀에는 그대로
  보여 "안 보이는 데이터"가 되지 않게 한다.
- **AI·부서·자동화 집계의 분모는 lv6 뿐**(`stats()` 의 `detail_total`). 전체 노드를 분모로 쓰면
  상세를 가질 수 없는 lv3~lv5 가 전부 "미적용"으로 잡혀 적용률이 왜곡된다.
- 한국어 UI 문장에 레벨 이름을 넣을 땐 `schema.josa()` 를 쓴다 — "부문은" / "대분류는".
  `"...은(는)"` 같은 표기 금지.
- **평면 노드 배열 + `parent_id`** (중첩 JSON 아님). 이동 = `parent_id`/`order` 2필드 수정.
  사이클은 `would_cycle()` 로 차단, `normalize()` 가 로드 시 `level` 을 깊이로 재계산한다.
- **lv0~lv2 는 노드가 아니다.** `schema.FIXED_LEVELS` 상수이고 lv3 의 `parent_id` 는 `ROOT_ID("__root__")`.
  엑셀 내보내기 시점에만 lv0~lv2 컬럼으로 재부착한다.
- **도메인 마스터는 `process_tree.json` 안에** (`domains` 키). 노드가 참조하는 값이라 항상 함께
  원자적으로 저장돼야 한다 — 파일을 쪼개면 "기술명 rename + 노드 일괄 치환"이 2회 write 로 갈라진다.
- **충돌 검사는 `rev`(단조 증가 정수)가 정본.** mtime 은 NAS 해상도·시계 스큐 때문에 신뢰할 수 없어
  "누가 방금 저장했다" 배너 감지용으로만 쓴다. 자동 병합은 하지 않는다.
- **스냅샷은 pre-image** — 강제 덮어쓰기를 해도 상대 버전이 이력에 남아 복원 가능하다.
- **최초 로드가 시드를 파일로 고정한다** (`load_tree` → `_create_if_absent`, O_EXCL).
  세션마다 `bootstrap()` 하면 같은 "선장운전"이 세션마다 다른 id 로 생겨, 최초 저장 전에
  내보낸 엑셀을 다른 세션에서 올릴 때 전부 신규로 잡혀 시드가 통째로 중복된다.
  이 동작을 빼지 말 것 — `_smoke.py` 에 회귀 테스트가 있다.
- **저장은 [저장] 버튼에서만.** 편집은 세션 메모리에만 반영한다(드래그마다 저장하면 스냅샷 폭발).
- **PII**: 카드·표·엑셀은 `mask_name` 마스킹, **상세 편집 입력 위젯만 원본**(마스킹하면 편집 불가).
  DnD 카드 payload 에는 마스킹된 이름만 실어 **원본이 iframe DOM 에 존재하지 않게** 한다.
- **폴백 (공통규칙 5) — v2 의 한계를 알고 있을 것**: UI 가 컴포넌트 하나뿐이라 v1 의 "간단 모드"
  같은 위젯 폴백이 **없다**. `frontend/` 자산이나 `pyarrow` 가 없으면 화면이 뜨지 않는다.
  그래서 `check_env.py` 가 둘 다 검사하고 `pyproject.toml` 이 pyarrow 를 고정한다 —
  이 방어선을 걷어내지 말 것. 데이터 쪽 폴백(파일 부재·손상 → bootstrap)은 `store.load_tree` 가 유지한다.

## 커스텀 컴포넌트 주의 (frontend/index.html)

npm 빌드 없는 정적 컴포넌트다. 아래는 Streamlit 번들에서 확인한 계약이라 어기면 조용히 죽는다.

- 모든 postMessage 에 **`isStreamlitMessage: true`** 필수 — 없으면 부모가 무시하고 60초 뒤 타임아웃.
  그래서 `send()` 한 곳에서만 postMessage 한다.
- 순서: `componentReady({apiVersion:1})` → 부모가 `streamlit:render` 송신 → `setFrameHeight` →
  (사용자 조작) `setComponentValue`. **ready 이전 메시지는 폐기된다.**
- **`render()` 안에서 `setComponentValue` 를 부르면 무한 리런**이다. 사용자 조작 콜백에서만 호출.
- iframe 은 `scrolling:"no"` — 내부 스크롤 불가. 컬럼별 `overflow-y:auto` + 정확한 `setFrameHeight` 필수.
- 되돌려주는 값은 **사용자 조작 1건**. `save`/`force` 는 프론트가 들고 있는 **트리 전체**를 실어 보낸다
  (v2 는 부분 화면이 아니라 전 트리를 갖고 있으므로 안전하다). `download` 도 저장 전 편집분을 함께 보낸다.
- 중복 처리 가드는 **`evt_id`(UUID)**. 단조 증가 seq 를 쓰면 iframe 새로고침 시 카운터가 1로 리셋돼
  이후 모든 이벤트가 영영 무시된다.
- Sortable 은 **`forceFallback: true`** (네이티브 HTML5 DnD 대신 pointer 이벤트). iframe 안에서
  동작이 일관되고 드래그 중 자동 스크롤을 얻는다. 이 때문에 `dragenter`/`dragleave` 는 발생하지 않아
  드롭존 강조는 `body.dragging .cdrop:hover` CSS 로 처리한다.
- group 이름을 레벨별(`L3`~`L6`)로 분리해 **lv4 카드가 lv6 컬럼에 떨어지는 사고를 구조적으로 차단**한다.
  드래그로 되는 것은 **같은 레벨 안의 순서 변경**과 **상위 바꾸기**뿐이다(레벨 유지).
  레벨 변경(승격/강등)은 현재 UI 에 없다 — 필요하면 상세 패널에 별도 조작을 만들 것.

## UI

- 텍스트는 한국어. Streamlit 사이드바는 쓰지 않는다 — 상단 헤더(브랜드·환경·AI적용률·작성자·저장)와
  탭 4종(계층 편집 / 도메인 관리 / 엑셀 가져오기·내보내기 / 이력·복원)이 모두 컴포넌트 안에 있다.
- 보드: `lv3 │ lv4 │ lv5 │ lv6` 컬럼 드릴다운(`▦ 컬럼`) + 들여쓰기 `☰ 전체보기` 두 모드.
  카드 클릭=선택, `＋`=추가, 드래그=순서 변경, 카드 안 드롭존=상위 바꾸기.
- 상세 필드는 lv6 만 — 상위 레벨은 이름+설명(프론트 `hasDetail`, 파이썬 `schema.has_detail`).
