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

**두 가지로 배포된다 — UI 소스는 `frontend/index.html` 한 벌뿐이다.**

| | 메인앱 (Streamlit, 8540) | 개인 배포판 (standalone HTML) |
|---|---|---|
| 용도 | 공용PC 한 대, 전체 트리 관리·취합 | 각자 PC 에서 자기 업무 작성 → JSON 제출 |
| 만드는 법 | `run.bat` | `python build_standalone.py` → `dist/프로세스설계_개인작성용.html` |
| 백엔드 | `app.py` + `store.py` | **없음** (localStorage + 파일 내보내기) |

**모드 축이 둘이다** (`index.html` 상단). 하나로 뭉치지 말 것 —
"백엔드는 없지만 데이터는 진짜"인 개인 배포판을 표현할 수 없다.
- `SOLO` = 빌드타임 상수 (`window.__PD_SOLO__`, `build_standalone.py` 가 주입)
- `IN_ST` = 런타임, "streamlit:render 를 받았는가" (전송 계층). **의미를 바꾸지 말 것**

| SOLO | IN_ST | 상태 |
|---|---|---|
| false | true | Streamlit 실서비스 |
| false | false | **디자인 단독 미리보기** (`seed()`/`MOCK_*` 목업) |
| true | — | **개인 배포판** (진짜 데이터, localStorage) |

`emit()` 이 이 세 갈래로 분기한다: `send` / `previewAction`(가짜) / `soloAction`(진짜).
`previewAction` 은 이름 그대로 **아무것도 저장하지 않는다** — `soloAction` 과 혼동 금지.

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

> v1(Streamlit 위젯 UI) 파일 `state.py`·`ui_styles.py`·`dnd_component.py`·`views/` 는 **삭제됐다**.
> 기능은 모두 `frontend/index.html` 로 옮겼다. 되살리지 말 것.

### 파이썬 ↔ 컴포넌트 이벤트

| 이벤트 | 방향 | 하는 일 |
|---|---|---|
| `save` / `force` | JS→PY | 트리 전체 전송 → `store.save_tree` (force = 충돌 무시 덮어쓰기) |
| `download` | JS→PY | 저장 전 편집분 포함해 xlsx/json 생성 → data URI 자동 다운로드 |
| `import` | JS→PY | 엑셀 **파싱·미리보기만**. 결과는 `pending_import` 에 보류 |
| `import_apply` | JS→PY | `{delete_missing, add_domains}` 옵션으로 보류분 반영 |
| `import_cancel` | JS→PY | 보류분 폐기 |
| `histpick` | JS→PY | 스냅샷 선택 → `schema.diff` 로 복원 미리보기 계산 |
| `restore` / `reload` | JS→PY | 스냅샷 복원 / 디스크 재로드 |

### 발생 패턴 — 부하 분석용 수집 (개인 배포판 SOLO 전용, 앱 미반영)

부하 = "언제·몇 번 하는가". lv6 은 `occur_pattern` 으로 세 갈래:

| 패턴 | 받는 값 | 부하 |
|---|---|---|
| 상시루틴 | `freq_unit` + `freq_count` | 연간 = 횟수 × 단위연간수 × 소요시간 |
| 호선루틴 | `freq_unit` + `freq_count` + `apply_phases[]`(복수) | **구간길이 미상 → 보류.** 나중에 trial_schedule 조인 |
| 호선이벤트 | `events[]` — 각 `{event, offset_start, offset_days}` (마일스톤 기준 ±일) | 호선당 = **지정한 시점 수** × 소요시간. 연간은 척수 곱해 나중에 |

lv4 는 `work_type`(일상/호선) + `ship_types[]`(선종 복수) 를 받고 **lv6 이 상속**한다
(`shipTypesOf` → `ancestors`). lv6 에 선종 필드를 두지 말 것 — 중복되면 어긋난다.

- `TRIAL_PHASES`(호선루틴 반복 구간) = 안벽→앵카링→시운전 순 7종:
  `LC~GT+1 · GT+1~IE · AC · 통합시운전 · GasT · ST,DP · 인도준비`. trial_schedule 일정구분과 조인.
- **lv3~lv5 붙여넣기**(`actPasteSkeleton`, `pageSoloIO`): 엑셀에서 세 열을 복사해 붙여넣으면
  뼈대를 한 번에 만든다. 브라우저는 `.xlsx` 를 못 읽어 **탭 구분 TSV** 로 받는다(라이브러리 없음).
  이름 경로로 dedup·병합하고 lv3 은 시드 부문 이름과 같으면 그 노드로 합쳐진다 — 두 번 붙여도 안 는다.

**지켜야 할 규칙:**
- **원자값만 저장, 곱셈은 저장 안 함.** 척수·구간길이·마일스톤 날짜·근무일은 전부 외부
  parquet 이고 시나리오마다 변한다. 지금 곱해 "연간부하"로 저장하면 척수 시나리오를 못 바꾼다.
  `loadOf()` 가 **`kind` 를 함께 반환**하는 이유 — 연간(상시)과 호선당(이벤트)을 한 숫자로
  더하면 거짓이다. `stats()`·상단 지표가 버킷을 나눠 쓴다.
- **호선루틴은 호선당 횟수를 저장하지 않는다** — `구간길이 × 주기` 파생. 그래서 주기는
  반드시 **단위당 N회(비율)** 로 받는다. 총량으로 받으면 구간길이와 합성 불가.
- **호선당 횟수를 숫자 칸으로 받지 않는다** — `events[]` 에 시점을 여러 개 지정하면 **줄 수가
  곧 호선당 횟수**다(GT-7, AC-7 → 2줄 = 2회). "2회" 라고만 적으면 *언제* 2번인지 안 남아
  일 단위 부하로 못 편다. 시점을 남기면 횟수·타이밍이 함께 잡힌다. 이 호선당 값을
  `annual_count`(연간) 와 섞지 말 것 — 단위가 달라 취합 오염·집계 단위혼합이 난다.
- `freq_unit`+`freq_count` 를 나눈 이유 — 기존 `frequency` 열거형은 `주 1회` 뿐이라 **`주 3회`
  를 표현 못 했다.** 이 분리로 상시·호선루틴이 같은 형태가 된다.
- **`MILESTONE_EVENTS` 는 운영 12종 전부**(tbm `_PJTEVNT_MAP` 키). 사외망 더미 milestone 에
  G/T 가 없는 건 나중 조인 시 결측 문제이지 수집 옵션에서 뺄 이유가 아니다.
- **개인 파일에 이름을 넣지 않는다** — `soloExport` 가 lv6 에 `dept` 만 주입하고 `owner`·
  `updated_by` 를 비운다. 취합은 소속별 집계다. **이름칸은 필수 아님**(소속만 필수) — 어차피
  제출 파일에서 지워지므로 `canSave`/`ready` 는 `!!S.dept` 만 본다.
- **세부 패널은 짧게, 요약은 카드로.** lv6 카드(`cardHTML`, SOLO)에 발생패턴·부하·자동화·선종을
  `.mc occ/load/auto/ship` 배지로 띄우고, 세부 패널(`occurBlock`)에서 부하 수치·선종 힌트 같은
  **읽기전용 요약은 뺐다**(미완성일 때만 한 줄 안내). 보드에서 한눈에 비교되고 패널 스크롤이 준다.
- **개인 배포판 탭 4종** — 계층 편집 / 도메인 관리 / 내보내기·불러오기 / **📖 사용법**(`pageManual`,
  SOLO 전용). 도메인 관리는 SOLO 에서 **수행 주기 목록을 감춘다**(발생 패턴으로 대체돼 안 씀).

### 앱 미반영 (다음 단계 — HTML 과 별도로 정리)

위 필드는 **`SOLO` 게이트**라 메인앱 화면엔 안 뜬다. 앱에 붙일 때 할 일:
- `schema.py`: 신규 필드 + **`NODE_DEFAULTS` 편입**(빠지면 `diff` 가 변경을 못 잡아 엑셀·취합
  미리보기가 "변경 0건" 이라 거짓말한다) + 상수 + `annual_hours` 좁힘 + `per_ship_hours` +
  `stats` 버킷 분리 + `GROUP_META_FIELDS`(lv4 필드를 `DETAIL_FIELDS` 에 넣으면 AI·부서
  적용률 분모가 오염된다)
- `excel_io.py`: 신규 컬럼. **엑셀 업로드는 data_manager 패턴 참조** — 엑셀을 열어 DataFrame
  으로 만들어 저장 (`esg_converter.py`, `parquet_io.save_parquet_atomic`). `events[]` 는
  객체 배열이라 엑셀 한 칸에 못 담는다 — JSON 문자열로 직렬화하거나 시점당 행 전개를 정할 것.
- `app.py`: 취합 시 신규 필드 미리보기·반영, **소속별 집계**
- 부하 엔진: costplan `cost_model.compute`/`_milestone_phase_starts`/`_dates` 이식.
  척수는 pjtlist 읽기전용 + 수동 시나리오 (스키마에 넣지 말 것)

### 프론트엔드 규칙 (index.html)

- **JS 로직은 파이썬과 쌍둥이다.** `hasDetail`/`maskName`/`josa`/`actMoveTo`/`wouldCycle`/
  `maxDepthBelow`/`cascadeLevels`/`actReorder` 는 각각 `schema.has_detail`/`pii.mask_name`/
  `schema.josa`/`apply_move`/`would_cycle`/`max_depth_below`/`_cascade_levels`/`apply_reorder` 와
  **동일 규칙**이어야 한다. 한쪽만 고치면 화면과 엑셀·저장 결과가 어긋난다.
- **판정은 파이썬에 맡긴다.** 복원 diff·엑셀 미리보기는 `schema.diff`/`excel_io.*` 가 계산해
  `args.diff_preview`/`args.import_preview` 로 내려온다. **JS 에 재구현하거나 상수로 채우지 말 것** —
  실제로 `[2,1,4,6][S.histPick]` 같은 하드코딩 목업이 실서비스에 노출된 적이 있다.
- **목업(`seed()`/`MOCK_HIST`/`MOCK_AUDIT`)은 `!IN_ST` 단독 미리보기에서만.** Streamlit 안에서
  빈 이력을 목업으로 대체하면 가짜 기록이 진짜처럼 보인다.
- **`args.tree` 는 `tree_epoch` 이 바뀔 때만 채택한다.** 매 렌더마다 덮으면 `download`·`histpick`
  같은 조회성 왕복에도 화면이 저장본으로 돌아가 미저장 편집이 사라진다. 파이썬은 세션 트리를
  교체할 때 반드시 `_set_data()` 를 거쳐 epoch 을 올린다.
- **되돌릴 수 없는 조작은 확인 모달**(`S.confirm`)을 거친다 — 자손 있는 삭제, 미저장 상태의 다시 읽기.
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
- **작업시간**: `work_hours`(1회 소요시간) × `annual_count`(연간 횟수) = 연간 공수.
  **곱한 값은 저장하지 않는다** — 두 원본과 어긋난다. `schema.annual_hours()` / JS `annualHours()`
  로만 계산한다. 엑셀의 `연간공수(h)` 컬럼도 쓰기 전용이라 `FIELD_COLS` 에 넣으면 안 된다
  (넣는 순간 `parse_excel` 이 파생값을 저장 필드로 역수입한다).
  `FREQ_ANNUAL`(주기→횟수 기본값)의 **자동 채움은 JS 에서만** 한다 — 파이썬이 채우면
  연간횟수를 일부러 비운 엑셀이 조용히 52 를 얻는다. 그리고 **비어 있을 때만** 채운다
  (손으로 친 숫자를 드롭다운 조작이 날리면 안 된다).
- **JS 숫자 입력은 강제하지 않는다.** 키 입력마다 rerender 하므로 `parseFloat` 로 스냅시키면
  `"0."` 이 `"0"` 이 돼 **소수점을 아예 못 친다**. 친 문자열 그대로 저장하고 정리는 파이썬에 맡긴다.
- **`SEED_LV3` 는 `(id, name)` 고정 쌍**이고 `frontend/index.html` 의 `SEED_LV3` 와
  **id·이름이 완전히 같아야 한다**. 개인 배포판과 메인앱이 같은 부문 id 를 써야 취합 시
  자동 병합된다 — 한쪽만 고치면 부문이 사람 수만큼 중복된다.
  (`seed()` 는 디자인 미리보기용 목업이라 다르다. 혼동 금지)
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
  카드 클릭=선택, `＋`=추가, 드래그=순서 변경, 카드 안 드롭존=상위 바꾸기(레벨 유지).
- **레벨 변경(승격/강등)은 상세 패널의 `상위 업무 바꾸기` selectbox 에서만** — 드래그는 group 이
  레벨별로 갈려 있어 구조적으로 불가능하다. 후보 목록에서 사이클·깊이 초과 대상은 미리 제외한다.
- 상세 필드는 lv6 만 — 상위 레벨은 이름+설명(프론트 `hasDetail`, 파이썬 `schema.has_detail`).
