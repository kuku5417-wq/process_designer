# 프로세스 설계 v2 — 프론트엔드 교체 안내 (옵션 B)

기존 Streamlit 앱의 UI 를 v2 디자인(웜 톤 · 상단바 통합 · 라이브 편집 사이드패널 ·
컬럼/아웃라인 전환)으로 **통째 교체**하는 방식입니다. 백엔드(저장·스냅샷·엑셀·충돌검사)는
그대로 재사용합니다.

## 무엇이 바뀌나

| | 기존 | v2 (옵션 B) |
|---|---|---|
| UI 위치 | Streamlit 사이드바 + 위젯 상세폼 + 컬럼 보드(컴포넌트) | **컴포넌트 하나가 전체 화면** |
| 편집 상태 | Streamlit session_state | **브라우저(컴포넌트)** 가 보유, [저장] 시 서버로 |
| 컴포넌트 반환값 | 이벤트 1건 | **트리 전체**(저장) 또는 요청 이벤트(다운로드/복원/가져오기) |
| Python 역할 | UI + 라우팅 + 저장 | **데이터 저장 API 만** |

## 적용 순서

1. 이 폴더의 **`frontend/index.html`** 로 기존 `process_designer/frontend/index.html` 을 교체합니다.
   - `sortable.min.js` 는 **더 이상 필요 없습니다**(드래그 대신 컬럼/아웃라인 UI). 지워도 됩니다.
2. 이 폴더의 **`app.py`** 로 기존 `process_designer/app.py` 를 교체합니다.
   - 기존 파일이 필요하면 `app_streamlit_legacy.py` 등으로 백업해 두세요.
3. `views/`, `dnd_component.py`, `ui_styles.py`, `state.py` 는 **더 이상 쓰이지 않습니다**(지워도 되고 둬도 무해).
   `store.py` · `schema.py` · `excel_io.py` · `path_config.py` · `pii.py` 는 **그대로 필요**합니다.
4. 실행:
   ```bash
   uv run streamlit run app.py --server.port 8540
   ```
5. 로직 회귀검증은 그대로:
   ```bash
   python _smoke.py
   ```
   (schema/store/excel 는 손대지 않았으므로 통과해야 합니다.)

## 동작 계약 (index.html ↔ app.py)

**app.py → 컴포넌트 (render args)**
```jsonc
{
  "tree":   { "nodes": [...], "domains": {...}, "rev": 12 },
  "author": "홍길동",           // 세션에 남은 작성자
  "env":    "사외망 · code_N/…",
  "history":[ {file, ts, author, rev, n_nodes}, ... ],
  "audit":  [ {ts, author, action, rev, n_nodes}, ... ],
  "flash":  "저장했습니다 (rev 13).",
  "conflict": null | { "disk_author": "이정호", "disk_rev": 14 },
  "disk_newer": null | { "rev": 14, "author": "이정호" },
  "dirty_all": false            // 엑셀 반영 직후 true → 전부 '미저장' 표시
}
```

**컴포넌트 → app.py (setComponentValue, evt_id 로 중복 차단)**
| type | 페이로드 | app.py 처리 |
|---|---|---|
| `save` / `force` | `rev, author, nodes, domains` | `store.save_tree(..., force=)` — 충돌 시 `conflict` 로 되돌림 |
| `download` | `fmt("xlsx"\|"json"), mask, nodes, domains` | `excel_io.build_*` → 브라우저 자동 다운로드 |
| `import` | `filename, b64` | `excel_io.parse_excel` → diff 병합(삭제 기본 OFF) → `dirty_all` |
| `restore` | `file, author` | `store.restore` |
| `reload` | — | `store.load_tree` 재로드 |

## 주의

- **pyarrow 는 계속 의존성에 두세요.** `streamlit.components.v1` 인스턴스 생성 시 import 되어,
  없으면 `StreamlitAPIException` 으로 죽습니다. (parquet 은 여전히 생산하지 않습니다.)
- **PII**: 카드·표·요약·이력은 모두 `mask_name` 마스킹된 값만 표시합니다. 상세 편집 입력칸(담당자)에만
  원본이 들어가며, 저장 시 서버로 전달되어 파일에 원본이 보관됩니다(기존과 동일 정책).
- **충돌/재읽기 배너**는 컴포넌트 상단에 뜹니다(`conflict` / `disk_newer` args). 자동 병합은 하지 않으며,
  강제 덮어써도 상대 버전은 스냅샷으로 복구 가능합니다.
- **레벨별 입력 범위**(lv6 만 상세)·**AI 집계 분모(lv6)**·**조사 처리** 등 규칙은 UI 에도 그대로 반영돼
  있지만, 정본은 여전히 `schema.py`(`has_detail` 등)입니다. 규칙을 바꾸면 `schema.py` 와 `index.html`
  양쪽을 함께 고쳐야 합니다.
- 컴포넌트는 `scrolling:"no"` 이므로 내부 스크롤(보드 컬럼·사이드패널·페이지)은 컴포넌트가 자체 처리합니다.
  화면이 좁으면 상단바가 잘릴 수 있어 `layout="wide"` 를 권장합니다.
