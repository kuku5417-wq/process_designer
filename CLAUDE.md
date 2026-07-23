# process_designer

시운전팀 **프로세스 설계 앱**. lv0 조선 › lv1 생산 › lv2 시운전(고정) 아래로 lv3~lv7 업무
계층을 카드로 그리고, 업무별 **AI 에이전트 적용(현재·향후)·활용 기술·담당 부서/과·연계시스템·
적용 선종·특이사항**을 기록한다. 공용PC에 띄워 여러 명이 함께 편집한다.

> 레벨: lv3 부문 · lv4 대분류 · lv5 중분류 · lv6 세부업무 · **lv7 단위작업**. 상세 입력 폼은
> **lv6·lv7 공통**이지만(입력 내용 동일), **부하·AI·부서 집계의 기준(분모)은 lv6** 이다.
> lv7 에 넣은 부하·기술은 부모 lv6 으로 **롤업**된다(자세히는 아래 "레벨 모델" 절).

## 실행

```bash
uv run streamlit run app.py --server.port 8540
```
- 최초 셋업: `setup_env.bat` (venv → check_env → 실행) / 이후: `run.bat`
- 가동 전 점검: `python check_env.py` (`--quick` = 쓰기 테스트 생략)
- 회귀검증: `python _smoke.py` — Streamlit 없이 schema/store/excel 151항목 검증. **로직 수정 후 반드시 실행.**
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
| `app.py` | 진입점 겸 저장 API — 컴포넌트 호스팅, 이벤트(save/force/import/collect/restore/reload) 처리 |
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
| `import` | JS→PY | 엑셀 **파싱·미리보기만**. 결과는 `pending_import` 에 보류 |
| `import_apply` | JS→PY | `{delete_missing, add_domains}` 옵션으로 보류분 반영 |
| `import_cancel` | JS→PY | 보류분 폐기 |
| `collect_scan` | JS→PY | 다수 제출 JSON 취합(폴더 경로 glob 또는 다중 업로드) → `excel_io.collect_jsons` 로 **경로 병합·인원수 집계**, `pending_collect` 에 보류 |
| `collect_apply` / `collect_cancel` | JS→PY | 취합 보류분 반영 / 폐기 (취합은 추가·병합만, 삭제 옵트인 없음) |
| `histpick` | JS→PY | 스냅샷 선택 → `schema.diff` 로 복원 미리보기 계산 |
| `restore` / `reload` | JS→PY | 스냅샷 복원 / 디스크 재로드 |

> **다운로드는 파이썬을 거치지 않는다(클라이언트 사이드).** 예전엔 `download` 이벤트로 파이썬이
> bytes 를 만들어 sandboxed iframe 의 data-URI 로 내려줬는데, 최신 Chrome 이 **사용자 제스처 밖
> sandbox 다운로드를 차단**해 실패했다. 지금은 프론트가 클릭 제스처 안에서 Blob 을 만들어
> 바로 내려받는다(`csvRows`/`csvDownload`/`jsonDownload`, 담당자 마스킹 유지). 메인앱 버튼도
> **"엑셀(CSV) 다운로드"** (진짜 `.xlsx` 아님 — 브라우저에 xlsx 라이브러리 없음). `app.py` 의 구
> `download` 핸들러는 사실상 죽은 경로다.

### 레벨 모델 — lv3~lv7, 상세는 lv6·lv7 / 집계는 lv6

- 레벨: lv3 **부문** · lv4 **대분류** · lv5 **중분류** · lv6 **세부업무** · lv7 **단위작업**.
- **상세 입력 폼은 lv6·lv7 공통** — `schema.has_detail(level)`(= `level >= FULL_DETAIL_LEVEL(6)`)이
  폼 노출을 결정하고, `>=` 라 lv7 도 자동 포함된다. **폼 게이트 전용 함수**로 의미를 좁혔다.
- **부하·AI·부서·자동화 집계의 기준(분모)은 lv6 뿐** — 신규 상수 **`schema.LOAD_LEVEL(=6)`**
  (JS `LOAD_LV`)로 고정한다. `stats()` 분모·`collect_jsons` 인원수·요약 byDept 는 `has_detail` 이
  아니라 `level == LOAD_LEVEL` 을 본다. (`has_detail` 을 그대로 쓰면 lv7 이 분모에 새어든다 — 함정.)
- **롤업**: 한 lv6 의 유효 부하 = 자신 `annual_hours` + Σ(lv7 자식 `annual_hours`)(가산). AI 적용 =
  자신 또는 lv7 자식 중 하나라도 기술 보유(현재·향후 각각). 즉 부하·기술은 lv6 에 직접 넣든 lv7 에
  넣어 롤업하든 같은 lv6 지표가 된다. 분모는 **모든 lv6**(lv7 유무 무관).
- 스키마 상수: `LEVEL_MAX = 7`, `LEVEL_MIN = 3`, `FULL_DETAIL_LEVEL = 6`(폼), `LOAD_LEVEL = 6`(집계).
  트리 조작 경계(`apply_move`/`_cascade_levels`/`validate`)·엑셀 열(`LV_COLS`)·DnD 그룹은 `LEVEL_MAX`
  상수 기반이라 자동으로 lv7 까지 확장된다 — 명시로 바꿔야 할 곳은 **집계 게이트뿐**.

### 상세 필드·도메인 (lv6·lv7 공통)

- **부서/과 2단**: `DEPT_TREE`(부서→과) + `dept_parent(과)` 로 과(leaf)만 노드에 저장하고 부서는 파생.
  선택 UI 는 `<optgroup>`, 집계는 `by_dept`(과별) + `by_dept_group`(부서 롤업).
- **AI 적용은 파생값**: `has_ai_agent = bool(tech)`(현재), `has_ai_future = bool(future_tech)`(향후).
  수동 토글은 없다 — `normalize()`(JS 토글 콜백)가 기술 선택 유무로 강제한다. 따라서 **AI 적용률 =
  "활용기술을 가진 lv6 비율"**(롤업 포함).
- 다중선택 도메인 칩: `tech`(현재·향후 공유) · `automation_level` · `ship_type`
  (CNT·COT·LNG·SHTL·VLAC·VLCC·FLNG) · `special_note`(SG·DF(LNG)·메탄올·LPG). **적용 선종·특이사항은
  메인앱 패널에도** 노출된다(발생 패턴이 호선일 때) — 예전의 "SOLO 전용" 이 아니다.
- **연계시스템 다건**: `linked_systems[{system,detail}]`(줄 추가/삭제, 호선이벤트 `events[]` 패턴 재사용).
  구 단일 `linked_system`/`linked_system_detail` 은 back-compat 로 두고 `normalize()` 가 신형으로 이관.
- 엑셀/CSV 는 보기용이라 리스트·객체배열을 조인 문자열로 쓰되, **다건 객체(`linked_systems`)는 파싱
  역수입하지 않는다**(JSON 이 정본). `withDefaultDomains(dom)` 가 로드 4지점에서 신규 도메인 키를
  백필해, 옛 초안에도 새 도메인(선종·특이사항)이 항상 뜬다.

### 발생 패턴 — 부하 분석용 수집 (발생패턴 입력 UI 는 개인 배포판 SOLO 전용)

부하 = "언제·몇 번 하는가". lv6·lv7 은 `occur_pattern` 으로 세 갈래(입력은 SOLO 에서만, 집계는 lv6 롤업):

| 패턴 | 받는 값 | 부하 |
|---|---|---|
| 상시루틴 | `freq_unit` + `freq_count` | 연간 = 횟수 × 단위연간수 × 소요시간 |
| 호선루틴 | `freq_unit` + `freq_count` + `apply_phases[]`(복수) | **구간길이 미상 → 보류.** 나중에 trial_schedule 조인 |
| 호선이벤트 | `events[]` — 각 `{event, offset_start, offset_days}` (마일스톤 기준 ±일) | 호선당 = **지정한 시점 수** × 소요시간. 연간은 척수 곱해 나중에 |

`ship_types[]`(선종 복수)는 호선 패턴(호선루틴·호선이벤트)일 때만 입력한다 — SOLO 는 발생 패턴
블록 안에서, **메인앱은 상세 패널에** 같은 칩으로(발생 패턴이 호선인 노드에 한해). 부하 계산이
`호선.선종 ∈ 업무.선종`으로 조인하는 **적용 필터**다 — 호선 리스트의 선종("이 배는 LNG선")과 역할이
다르다(없으면 모든 호선 업무가 전 선종에 적용돼 부하가 부풀려진다). 상시루틴은 호선 무관이라 선종이
없다. `shipTypesOf(id)`는 **노드 자신의 `ship_types`**를 돌려준다(카드 배지용). 선종 도메인은 `ship_type`
(도메인 관리로 편집).

> `work_type`(일상/호선)은 **폐지**했다 — 발생 패턴(상시루틴=일상, 호선루틴·호선이벤트=호선)과 완전히
> 중복이고 부하 계산이 전부 발생 패턴으로 분기하므로 쓰이지 않는다. `coerceEvents`가 구 필드를 정리한다.

- `TRIAL_PHASES`(호선루틴 반복 구간) = 안벽→앵카링→시운전 순 7종:
  `LC~GT+1 · GT+1~IE · AC · 통합시운전 · GasT · ST,DP · 인도준비`. trial_schedule 일정구분과 조인.
- **lv3~lv7 붙여넣기**(`actPasteSkeleton`, `pageSoloIO`): 엑셀에서 **최대 다섯 열**(부문·대분류·
  중분류·세부업무·단위작업)을 복사해 붙여넣으면 뼈대를 한 번에 만든다. 열이 적으면 그 깊이까지만
  만든다. 브라우저는 `.xlsx` 를 못 읽어 **탭 구분 TSV** 로 받는다(라이브러리 없음). 왼쪽부터 연속
  채움만 허용(중간 빈 열이 있는 줄은 건너뜀). 이름 경로로 dedup·병합하고 lv3 은 시드 부문 이름과
  같으면 그 노드로 합쳐진다 — 두 번 붙여도 안 는다.

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

### 시드 배포 · lv3 칩 · 다중 드래그

- **과별 lv5 골격 배포**: `build_standalone.py --seed <파일>`(단일) / `--seeds <폴더>`(과별 일괄) /
  `--out`. 시드 JSON 을 `window.__PD_SEED__` 로 주입해 개인 배포판의 기본 트리를 과별로 다르게 낸다.
  boot 시 `soloSeed` 대신 `__PD_SEED__` 를 채택(있으면). `_safe_json` 이 `</` → `<\/` 이스케이프.
- **lv3 부문 = 컬러 칩 선택**(`crumbBar`, `LV3_COLORS`/`lv3Color`). 빵부스러기 드롭다운을 12색 칩으로
  바꿔, 칩 클릭=lv3 선택, 카드를 칩에 드롭=그 부문으로 이동(lv3 변경). 칩 드롭 타깃 그룹은 `L4`.
- **다중선택 드래그**(SortableJS MultiDrag, 번들에 포함): `multiDrag:true, multiDragKey:"CTRL",
  selectedClass:"multisel"`. Ctrl+클릭으로 여러 카드를 골라 한 번에 옮긴다 → `actMoveMany(ids,newPid)`.

### 남은 것 (부하 엔진)

집계·상세는 앱에 반영됐다. 남은 것은 **실제 부하 수치 산출**:
- 부하 엔진: costplan `cost_model.compute`/`_milestone_phase_starts`/`_dates` 이식. 발생 패턴별
  버킷(연간/호선당/보류)을 척수·구간길이·마일스톤 날짜와 조인해 연간부하로 편다.
  척수는 pjtlist 읽기전용 + 수동 시나리오 (**스키마에 넣지 말 것** — 곱한 값 저장 금지 규칙).
- `apply_phases[]`(호선루틴 구간길이 미상) 는 trial_schedule 조인 시 채워진다.

### 프론트엔드 규칙 (index.html)

- **JS 로직은 파이썬과 쌍둥이다.** `hasDetail`/`maskName`/`josa`/`actMoveTo`/`wouldCycle`/
  `maxDepthBelow`/`cascadeLevels`/`actReorder` 는 각각 `schema.has_detail`/`pii.mask_name`/
  `schema.josa`/`apply_move`/`would_cycle`/`max_depth_below`/`_cascade_levels`/`apply_reorder` 와
  **동일 규칙**이어야 한다. 한쪽만 고치면 화면과 엑셀·저장 결과가 어긋난다. 상수·도메인·필드도
  트윈이다 — `LABELS`↔`LEVEL_LABELS`, `LOAD_LV`↔`LOAD_LEVEL`, `SEED_LV3`·`DEPT_TREE`·`DEF_DOMAINS`,
  파생 AI(`has_ai_agent=bool(tech)`, `has_ai_future=bool(future_tech)`)는 JS 토글 콜백과
  `schema.normalize` 양쪽에서 강제한다. `withDefaultDomains(JS)` ↔ normalize 의 도메인 백필.
- **판정은 파이썬에 맡긴다.** 복원 diff·엑셀 미리보기는 `schema.diff`/`excel_io.*` 가 계산해
  `args.diff_preview`/`args.import_preview` 로 내려온다. **JS 에 재구현하거나 상수로 채우지 말 것** —
  실제로 `[2,1,4,6][S.histPick]` 같은 하드코딩 목업이 실서비스에 노출된 적이 있다.
- **목업(`seed()`/`MOCK_HIST`/`MOCK_AUDIT`)은 `!IN_ST` 단독 미리보기에서만.** Streamlit 안에서
  빈 이력을 목업으로 대체하면 가짜 기록이 진짜처럼 보인다.
- **`args.tree` 는 `tree_epoch` 이 바뀔 때만 채택한다.** 매 렌더마다 덮으면 `download`·`histpick`
  같은 조회성 왕복에도 화면이 저장본으로 돌아가 미저장 편집이 사라진다. 파이썬은 세션 트리를
  교체할 때 반드시 `_set_data()` 를 거쳐 epoch 을 올린다.
- **자유 텍스트 입력(이름·설명)은 매 키 입력마다 `rerender()` 하지 않는다.** `rerender` 는
  `contentEl.innerHTML` 을 통째로 교체하고 `wireDnD()` 로 Sortable 을 다시 붙이는데, 그 사이
  포커스된 `<input>` 이 파괴돼 **빠르게 치면 글자가 사라진다**("계층 입력 시 한번씩"). 이름·설명은
  모델만 갱신하고 화면의 메아리(`echoName`/`echoDesc` — 카드 `.nm`/`.cdesc`, 전체보기 `.rowname`/
  `.rowdesc`, 패널 `.pname`)만 제자리에서 고치고, **칸을 벗어날 때(onChange) 한 번만** 전체를 다시
  그려 동기화한다(미저장 점·빵부스러기·설명 카드 생성). 숫자 필드는 부하 배지 실시간 갱신이 필요해
  기존 rerender 경로를 유지한다.
- **되돌릴 수 없는 조작은 확인 모달**(`S.confirm`)을 거친다 — 자손 있는 삭제, 미저장 상태의 다시 읽기.
- **드래그 콜백(onEnd/onAdd)에서 곧바로 `rerender()` 하지 말 것.** `rerender` → `wireDnD` 가
  드래그 중인 Sortable 을 destroy 해 `onEnd` 가 영영 오지 않고, `dragging` 클래스와 `_dragging`
  가드가 남아 **앱 전체가 클릭 불능**이 된다. 반드시 `setTimeout(...,0)` 으로 다음 틱에 미룬다.
- DnD group 은 레벨별(`L3`~`L7`)로 분리 — 같은 레벨끼리만 오가므로 레벨 변경·사이클이 원천 불가능하다.
  그룹명은 `data-lv` 로 `L{lv}` 자동 생성이라 lv7 까지 자동 확장되지만, 드래그 CSS(`dragL7`/`acc7`)와
  드롭존 게이트(`cardHTML` 의 `lv < LEVEL_MAX`)는 명시로 lv7 을 열어줘야 한다.
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

- **상세 폼은 lv6·lv7 공통, 집계 분모는 lv6.** lv3~lv5 는 업무를 묶는 분류 그룹이라 **이름 + 설명**만
  받는다. 폼 노출 정본은 `schema.has_detail(level)`(`>= FULL_DETAIL_LEVEL(6)`) — 상세 패널(`panel`),
  카드 칩(`cardHTML`)이 이 함수 하나를 본다. **집계(부하·AI·부서)의 분모는 `LOAD_LEVEL(6)`** 로 따로
  고정한다 — `stats()`·`collect_jsons` 인원수·요약 byDept 는 `has_detail` 이 아니라 `level == LOAD_LEVEL`
  을 본다. 두 개념(폼/집계)을 한 함수로 뭉치지 말 것. lv7 값은 부모 lv6 으로 롤업된다.
- **레벨이 바뀌어도 상세 값은 지우지 않는다** — 화면에서 숨길 뿐이다. lv6→lv5 로 승격했다가
  되돌리면 값이 그대로 살아난다(실수 복구). `normalize()` 가 상위 레벨 필드를 비우게 만들지 말 것.
  숨은 값은 `schema.has_hidden_detail(node)` 로 감지해 상세 패널에 안내하고, 엑셀에는 그대로
  보여 "안 보이는 데이터"가 되지 않게 한다.
- **AI·부서·자동화 집계의 분모는 lv6 뿐**(`stats()` 의 `detail_total`, `level == LOAD_LEVEL`). 전체
  노드를 분모로 쓰면 상세를 가질 수 없는 lv3~lv5 가 전부 "미적용"으로 잡혀 적용률이 왜곡되고,
  lv7 을 넣으면 롤업 대상이 분모에 중복으로 새어든다. **부하·AI 는 lv6 마다 자신+lv7 자식으로 롤업**
  (`rollup_hours` / JS `stats` 의 자식 합산, `has_ai_agent` OR).
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
- group 이름을 레벨별(`L3`~`L7`)로 분리해 **lv4 카드가 lv6 컬럼에 떨어지는 사고를 구조적으로 차단**한다.
  드래그로 되는 것은 **같은 레벨 안의 순서 변경**과 **상위 바꾸기**뿐이다(레벨 유지).
  레벨 변경(승격/강등)은 현재 UI 에 없다 — 필요하면 상세 패널에 별도 조작을 만들 것.

## UI

- 텍스트는 한국어. Streamlit 사이드바는 쓰지 않는다 — 상단 헤더(브랜드·환경·AI적용률·작성자·저장)와
  탭 4종(계층 편집 / 도메인 관리 / 엑셀 가져오기·내보내기 / 이력·복원)이 모두 컴포넌트 안에 있다.
- 보드: `lv3 │ lv4 │ lv5 │ lv6 │ lv7` 컬럼 드릴다운(`▦ 컬럼`) + 들여쓰기 `☰ 전체보기` 두 모드.
  카드 클릭=선택, `＋`=추가, 드래그=순서 변경, 카드 안 드롭존=상위 바꾸기(레벨 유지).
- **레벨 변경(승격/강등)은 상세 패널의 `상위 업무 바꾸기` selectbox 에서만** — 드래그는 group 이
  레벨별로 갈려 있어 구조적으로 불가능하다. 후보 목록에서 사이클·깊이 초과 대상은 미리 제외한다.
- 상세 필드는 lv6·lv7 — 상위 레벨(lv3~lv5)은 이름+설명(프론트 `hasDetail`, 파이썬 `schema.has_detail`).
  집계 분모만 lv6(`LOAD_LEVEL`).
