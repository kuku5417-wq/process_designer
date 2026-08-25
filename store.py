"""store.py — 프로세스 계층도 JSON 저장소 (원자적 저장 · 스냅샷 이력 · 충돌 검사).

저장 패턴은 data_manager/parquet_io.py 의 save_parquet_atomic 을 그대로 이식했다
(같은 폴더에 .{uuid}.tmp → os.replace 원자적 rename → finally 정리). 다만 이 앱은
parquet 을 생산하지 않으므로 JSON 판이다.

충돌 검사는 mtime 이 아니라 파일 안의 rev(단조 증가 정수)를 정본으로 쓴다 —
NAS/네트워크 공유는 mtime 해상도가 거칠고 시계 스큐가 있어 신뢰할 수 없다.
mtime 은 "누가 방금 저장했다" 배너 감지용으로만 쓴다.

스냅샷은 pre-image(덮어쓰기 직전의 디스크본)다. 그래야 "복원 = 그 시점으로 되돌리기"가
직관적으로 성립하고, 강제 덮어쓰기를 해도 남의 작업을 되살릴 수 있다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import path_config as pc
import schema


@dataclass(frozen=True)
class SaveResult:
    """저장 결과. conflict=True 면 디스크가 더 최신이라 저장하지 않았다는 뜻."""
    ok: bool
    conflict: bool = False
    rev: int = 0
    disk_rev: int = 0
    disk_author: str = ""
    disk_updated_at: str = ""
    error: str = ""
    version: int = 0            # 보관본 저장 시 부여된 버전 번호 (save_named 전용)
    # ── 부분 저장(save_merge) 결과 ──
    merged: bool = False        # 부분 병합으로 저장됐는가 (전체 교체와 구분)
    n_applied: int = 0          # 내 변경 중 실제로 반영된 노드 수
    n_deleted: int = 0          # 삭제된 노드 수 (자손 포함)
    n_cascade: int = 0          # 그중 **남이 그 아래 추가한 것**까지 함께 지운 수
    overlap: tuple = ()         # 남도 손댄 노드 id (차단하지 않고 알리기만 한다)
    dom_kept: bool = False      # 도메인 변경을 반영하지 못하고 디스크본을 유지했는가
    # ── 되살리기 확인 (남이 지운 업무를 내가 고쳤을 때) ──
    revive_ask: tuple = ()      # 판단이 필요한 노드 id. **비어 있지 않으면 아무것도 쓰지 않았다**
    n_revived: int = 0          # 되살린 수
    n_dropped: int = 0          # 삭제를 따르느라 버린 내 수정 수


# ── 원자적 저장 ─────────────────────────────────────────

def save_json_atomic(obj: dict, path: str | Path) -> Path:
    """obj 를 path 에 원자적으로 저장.

    같은 디렉토리에 .{uuid}.tmp 로 먼저 쓴 뒤 os.replace 로 교체한다.
    (os.replace 는 동일 볼륨에서 원자적. 임시파일을 같은 폴더에 둬서 보장.)
    읽는 쪽은 항상 완전한 파일만 보게 된다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    return path


# ── 로드 / 디스크 상태 ──────────────────────────────────

def disk_stat() -> tuple[float, int, str]:
    """(mtime, rev, updated_by). 파일이 없거나 읽을 수 없으면 (0.0, 0, "")."""
    p = pc.tree_path()
    try:
        if not p.exists():
            return 0.0, 0, ""
        mtime = p.stat().st_mtime
        raw = json.loads(p.read_text(encoding="utf-8"))
        return mtime, int(raw.get("rev", 0)), str(raw.get("updated_by", ""))
    except Exception:
        return 0.0, 0, ""


def _create_if_absent(obj: dict, path: Path) -> bool:
    """파일이 없을 때만 생성 (O_EXCL). 이미 있으면 False — 남의 파일을 덮지 않는다."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def load_tree() -> tuple[dict, list[str]]:
    """정본 트리 로드. (data, 경고메시지목록).

    파일이 없으면 bootstrap 을 **파일로 고정한 뒤** 반환한다. 세션마다 새로 bootstrap 하면
    같은 "선장운전"이 세션마다 다른 id 로 생겨, 최초 저장 전에 내보낸 엑셀을 다른 세션에서
    올릴 때 전부 신규로 잡혀 시드가 통째로 중복된다.

    손상 시에는 손상본을 보존한 채 bootstrap 으로 폴백한다.
    어떤 경우에도 앱이 죽지 않는다 (공통규칙 5).
    """
    warns: list[str] = []
    p = pc.tree_path()
    if not p.exists():
        seed = schema.bootstrap()
        if _create_if_absent(seed, p):
            return seed, warns
        # 그 사이 다른 사람이 만들었다면 그 파일을 정본으로 읽는다 (아래로 진행)
        if not p.exists():
            return seed, warns          # 쓰기 실패(권한 등) — 메모리 시드로라도 뜬다
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        # 손상 파일은 삭제하지 않고 보존 — 수동 복구 여지를 남긴다
        try:
            bak = p.with_suffix(f".corrupt_{datetime.now():%Y%m%d_%H%M%S}.json")
            p.replace(bak)
            warns.append(f"데이터 파일을 읽을 수 없어 기본값으로 시작합니다. 손상본 보관: {bak.name} ({e})")
        except Exception:
            warns.append(f"데이터 파일을 읽을 수 없어 기본값으로 시작합니다. ({e})")
        return schema.bootstrap(), warns

    errs = schema.validate(data)
    if errs:
        warns.append("데이터에 구조 오류가 있어 자동 보정했습니다: " + " / ".join(errs[:5]))
    try:
        data = schema.normalize(data)
    except Exception as e:
        warns.append(f"데이터 보정에 실패해 기본값으로 시작합니다. ({e})")
        return schema.bootstrap(), warns
    return data, warns


# ── 스냅샷 / 감사로그 ───────────────────────────────────

_SNAP_RE = re.compile(r"^process_tree_(\d{8})_(\d{6})_(.*)\.json$")


def _safe_name(s: str) -> str:
    """파일명에 쓸 수 없는 문자 제거."""
    return re.sub(r"[^\w가-힣.-]", "_", (s or "unknown").strip())[:20] or "unknown"


def _safe_label(s: str) -> str:
    """보관본 이름 → 파일명. `_safe_name` 과 **따로 두는 이유**: 저 함수는 스냅샷 파일명의
    작성자 조각용이라 20자로 자르는데, 보관본은 사람이 알아볼 이름이라 그 길이로는 잘린다.
    (`_safe_name` 을 늘리면 스냅샷 파일명 규약 `_SNAP_RE` 가 흔들린다 — 건드리지 않는다.)
    """
    out = re.sub(r"[^\w가-힣 .-]", "_", (s or "").strip())
    out = re.sub(r"\s+", " ", out).strip()[:60]
    return out


def snapshot(author: str) -> Path | None:
    """현재 디스크본을 history/ 로 복사 (pre-image). 파일이 없으면 None."""
    src = pc.tree_path()
    if not src.exists():
        return None
    try:
        dst = pc.get_history_dir() / f"process_tree_{datetime.now():%Y%m%d_%H%M%S}_{_safe_name(author)}.json"
        dst.write_bytes(src.read_bytes())
        return dst
    except Exception:
        return None


def audit(rec: dict) -> bool:
    """저장 감사로그 1줄 append. 실패해도 저장 흐름을 막지 않는다(best-effort)."""
    try:
        rec = {"ts": schema.now_iso(), **rec}
        with pc.audit_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_audit(limit: int = 100) -> list[dict]:
    try:
        p = pc.audit_path()
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        out.reverse()
        return out
    except Exception:
        return []


def list_history(limit: int = 200) -> list[dict]:
    """스냅샷 목록 (최신순). {file, ts, author, rev, n_nodes}."""
    out: list[dict] = []
    try:
        files = sorted(pc.get_history_dir().glob("process_tree_*.json"), reverse=True)[:limit]
    except Exception:
        return out
    pinned = load_pins()
    for f in files:
        m = _SNAP_RE.match(f.name)
        ts = ""
        author = ""
        if m:
            try:
                ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = m.group(1)
            author = m.group(3)
        rev, n_nodes = 0, 0
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            rev = int(raw.get("rev", 0))
            n_nodes = len(raw.get("nodes", []))
            author = str(raw.get("updated_by", "")) or author
        except Exception:
            pass
        # `pinned` 를 여기 실으면 app.py 를 손대지 않아도 프론트까지 그대로 흘러간다
        # (`_args` 가 `store.list_history()` 를 통째로 싣는다).
        out.append({"file": f.name, "ts": ts, "author": author, "rev": rev,
                    "n_nodes": n_nodes, "pinned": f.name in pinned})
    return out


def load_snapshot(name: str) -> dict | None:
    """스냅샷 로드. 경로 조작 방지를 위해 파일명만 받는다."""
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = pc.get_history_dir() / name
    try:
        if not p.exists():
            return None
        return schema.normalize(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def load_pins() -> set[str]:
    """고정된 스냅샷 파일명 집합.

    ★ **절대 예외를 던지지 않는다.** `prune_history` 가 이걸 부르고, `prune_history` 는
      `save_tree` 안에서 불린다 — 여기서 죽으면 **저장이 죽는다**(공통규칙 5 폴백).
      파일이 없거나 깨졌으면 "고정 없음"으로 본다.
    """
    try:
        p = pc.pins_path()
        if not p.exists():
            return set()
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {str(x) for x in (raw.get("files") or []) if str(x).strip()}
    except Exception:      # noqa: BLE001 — 손상 파일 때문에 저장이 막히면 안 된다
        return set()


_TOMB_MAX = 2000            # 최근 것만 남긴다 — 오래된 묘비는 아무도 그 시점 트리를 들고 있지 않다


def load_tombs() -> dict[str, dict]:
    """삭제된 노드 id → {"rev": 지운 시점 rev, "by": 지운 사람}.

    ★ **절대 예외를 던지지 않는다**(`load_pins` 와 같은 이유) — 저장 경로 안에서 불린다.
    """
    try:
        p = pc.tombs_path()
        if not p.exists():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for k, v in (raw.get("tombs") or {}).items():
            if isinstance(v, dict) and str(k).strip():
                out[str(k)] = {"rev": int(v.get("rev", 0)), "by": str(v.get("by", ""))}
        return out
    except Exception:      # noqa: BLE001 — 손상 파일 때문에 저장이 막히면 안 된다
        return {}


def _record_tombs(ids, rev: int, author: str) -> None:
    """지운 id 를 기록. **best-effort** — 실패해도 저장을 무르지 않는다.

    상한을 두는 이유: 묘비는 "내 rev 이후에 지워졌는가"를 답하기 위한 것이라 최신 것만 쓸모가 있다.
    무한히 쌓으면 매 저장마다 읽고 쓰는 파일이 계속 커진다.
    """
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return
    try:
        tombs = load_tombs()
        for i in ids:
            tombs[i] = {"rev": int(rev), "by": author or ""}
        if len(tombs) > _TOMB_MAX:
            keep = sorted(tombs.items(), key=lambda kv: kv[1].get("rev", 0), reverse=True)[:_TOMB_MAX]
            tombs = dict(keep)
        save_json_atomic({"tombs": tombs}, pc.tombs_path())
    except Exception:      # noqa: BLE001 — 기록 실패가 저장을 되돌리게 하지 않는다
        pass


def _disk_node_ids() -> set[str]:
    """디스크 정본의 노드 id 집합. 실패하면 빈 집합(= 이번엔 삭제 기록을 남기지 않는다)."""
    try:
        raw = json.loads(pc.tree_path().read_text(encoding="utf-8"))
        return {str(n.get("id")) for n in (raw.get("nodes") or [])
                if isinstance(n, dict) and n.get("id")}
    except Exception:      # noqa: BLE001
        return set()


def set_pin(name: str, pinned: bool) -> bool:
    """스냅샷 하나를 고정/해제. 고정된 것은 오래돼도 `prune_history` 가 지우지 않는다.

    ★ 파일명 검증을 `load_snapshot` 과 **같은 수준**으로 건다 — 임의 문자열이 핀 목록에 들어가면
      나중에 그 이름의 파일이 생겼을 때 뜻하지 않게 보호된다. `_SNAP_RE` 매칭까지 요구해
      `_audit.jsonl`·`_pins.json` 자신 같은 것이 못 들어오게 한다.
    ★ 유령 핀(사람이 파일을 직접 지운 경우) 정리는 **여기서만** 한다 — 읽을 때마다 정리하면
      매 렌더가 쓰기가 된다.
    """
    if not name or "/" in name or "\\" in name or ".." in name or not _SNAP_RE.match(name):
        return False
    try:
        hd = pc.get_history_dir()
        pins = load_pins()
        if pinned:
            if not (hd / name).exists():
                return False              # 없는 스냅샷은 고정하지 않는다
            pins.add(name)
        else:
            pins.discard(name)
        pins = {n for n in pins if (hd / n).exists()}      # 유령 핀 정리
        save_json_atomic({"files": sorted(pins)}, pc.pins_path())
        return True
    except Exception:      # noqa: BLE001 — 핀 실패로 앱이 죽지 않게
        return False


def prune_history(keep_days: int = 90, keep_min: int = 50) -> int:
    """오래된 스냅샷 정리. 최근 keep_days 일 전량 + 최신 keep_min 개는 항상 보존."""
    try:
        files = sorted(pc.get_history_dir().glob("process_tree_*.json"), reverse=True)
    except Exception:
        return 0
    if len(files) <= keep_min:
        return 0
    cutoff = time.time() - keep_days * 86400
    # ★ `files[keep_min:]` 슬라이스는 **건드리지 않는다.** 핀을 슬라이스 밖으로 빼면 keep_min 이
    #   "비핀 50개"라는 **다른 규칙**이 돼, 핀이 늘수록 일반 스냅샷 보존량이 줄어든다.
    pinned = load_pins()
    removed = 0
    for f in files[keep_min:]:
        try:
            if f.name in pinned:
                continue                  # 고정된 스냅샷은 오래돼도 남긴다
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            continue
    return removed


# ── 이름 붙인 보관본 (saves/<이름>/v0001_….json) ─────────
# 자동 스냅샷(history/)과 목적이 다르다: 저쪽은 "덮어쓰기 직전 상태"를 기계가 남기는 안전망이고,
# 이쪽은 "2026 상반기안" 처럼 **사람이 의미를 붙여 남기는 계보**다. 자동 정리 대상이 아니다.
#
# ★ 같은 이름으로 다시 저장하면 **덮지 않고 다음 버전으로 쌓는다.** 예전엔 덮어쓰기였는데,
#   보관본에는 pre-image 스냅샷이 없어 덮는 순간 이전 안이 영영 사라졌다. 버전이 쌓이면
#   "같은 이름 = 하나의 계보"가 되고 어느 버전으로든 되돌아갈 수 있다.
# ★ 이름을 **폴더**로 쓴다(파일명에 버전을 이어붙이지 않는다). 이름에 구분자로 쓸 만한 문자가
#   들어가도 파싱이 깨지지 않고, 버전 나열이 glob 한 번으로 끝나며, 이름 단위 삭제가 폴더 삭제다.

_VER_RE = re.compile(r"^v(\d{4,})_(\d{8})_(\d{6})\.json$")


def _group_dir(name: str) -> Path | None:
    """보관본 이름 → 폴더. 경로 탈출(`.`/`..`)은 여기서 막는다.

    `_safe_label` 이 `.` 과 `-` 를 남기므로(정상적인 이름에 필요하다) `..` 만 넣으면 상위 폴더가
    된다. 라벨 정규화만 믿지 말고 이 한 겹을 반드시 둘 것.
    """
    label = _safe_label(name)
    if not label or label.startswith("."):
        return None
    return pc.get_saves_dir() / label


def _versions_in(gdir: Path) -> list[tuple[int, Path]]:
    """폴더 안의 (버전번호, 경로) 를 오름차순으로. 규약에 안 맞는 파일은 조용히 무시한다."""
    out: list[tuple[int, Path]] = []
    try:
        for f in gdir.glob("v*.json"):
            m = _VER_RE.match(f.name)
            if m:
                out.append((int(m.group(1)), f))
    except Exception:
        return []
    out.sort()
    return out


def save_named(data: dict, name: str, author: str) -> SaveResult:
    """현재 트리를 `saves/<이름>/v{다음번호}_….json` 으로 보관한다. **항상 새 버전이 된다.**

    ★ rev 를 올리지 않는다. 보관본은 정본이 아니라 사본이라, 올리면 정본과 번호가 경합해
      "디스크가 더 최신" 오판이 난다. 지금 rev 를 참고값으로 적어 둘 뿐이다.
    """
    if not (author or "").strip():
        return SaveResult(ok=False, error="저장자를 입력해 주세요.")
    gdir = _group_dir(name)
    if gdir is None:
        return SaveResult(ok=False, error="보관본 이름을 입력해 주세요.")
    try:
        gdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return SaveResult(ok=False, error=f"보관 폴더를 만들 수 없습니다: {e}")
    nextv = (_versions_in(gdir)[-1][0] + 1) if _versions_in(gdir) else 1
    try:
        out = schema.normalize(dict(data))
        out["saved_name"] = gdir.name
        out["saved_version"] = nextv
        out["saved_at"] = schema.now_iso()
        out["saved_by"] = author
        save_json_atomic(out, gdir / f"v{nextv:04d}_{datetime.now():%Y%m%d_%H%M%S}.json")
    except Exception as e:
        return SaveResult(ok=False, error=f"보관에 실패했습니다: {e}")
    audit({"author": author, "rev": int(out.get("rev", 0)), "n_nodes": len(out["nodes"]),
           "action": "save_as", "name": gdir.name, "version": nextv})
    return SaveResult(ok=True, rev=int(out.get("rev", 0)), version=nextv)


def list_saves(limit: int = 100) -> list[dict]:
    """보관본 목록 — **이름 단위로 묶고 버전을 최신순으로** 담는다.

    [{name, n_versions, latest_version, ts, author, n_nodes, versions:[{version, ts, author, rev, n_nodes}]}]
    대표값(ts/author/n_nodes)은 **최신 버전** 것이다 — 목록 한 줄만 봐도 최근 상태를 알 수 있게.
    """
    out: list[dict] = []
    try:
        gdirs = sorted(d for d in pc.get_saves_dir().iterdir() if d.is_dir())
    except Exception:
        return out
    for g in gdirs:
        vs: list[dict] = []
        for ver, f in _versions_in(g):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue                  # 손상 파일은 목록에서만 빼고 파일은 남긴다
            if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
                continue
            vs.append({"version": ver,
                       "ts": str(raw.get("saved_at") or "").replace("T", " ")[:19],
                       "author": str(raw.get("saved_by") or ""),
                       "rev": int(raw.get("rev", 0) or 0),
                       "n_nodes": len(raw["nodes"])})
        if not vs:
            continue
        vs.sort(key=lambda v: v["version"], reverse=True)
        top = vs[0]
        out.append({"name": g.name, "n_versions": len(vs), "latest_version": top["version"],
                    "ts": top["ts"], "author": top["author"], "n_nodes": top["n_nodes"],
                    "versions": vs[:50]})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:limit]


def load_named(name: str, version: int | None = None) -> dict | None:
    """보관본 로드. version 이 없으면 **최신 버전**. 경로 탈출은 `_group_dir` 이 막는다."""
    gdir = _group_dir(name)
    if gdir is None:
        return None
    vs = _versions_in(gdir)
    if not vs:
        return None
    if version is None:
        target = vs[-1][1]
    else:
        hit = [p for v, p in vs if v == int(version)]
        if not hit:
            return None
        target = hit[0]
    try:
        return schema.normalize(json.loads(target.read_text(encoding="utf-8")))
    except Exception:
        return None


def delete_named(name: str, version: int | None = None) -> bool:
    """version 을 주면 그 버전 하나, 안 주면 **이름(계보) 통째로** 삭제. 되돌릴 수 없다."""
    gdir = _group_dir(name)
    if gdir is None or not gdir.exists():
        return False
    try:
        if version is None:
            shutil.rmtree(gdir)
            return True
        for v, p in _versions_in(gdir):
            if v == int(version):
                p.unlink()
                if not _versions_in(gdir):      # 마지막 버전을 지웠으면 빈 폴더도 치운다
                    try:
                        gdir.rmdir()
                    except Exception:           # noqa: BLE001 — 빈 폴더 정리는 실패해도 무해
                        pass
                return True
        return False
    except Exception:
        return False


# ── 저장 (충돌 검사 포함) ───────────────────────────────

def save_tree(data: dict, author: str, force: bool = False, action: str = "save") -> SaveResult:
    """트리 저장. 디스크 rev 가 내 rev 보다 크면 conflict 로 거부(force 로 덮어쓰기).

    덮어쓰기 직전 디스크본을 스냅샷으로 남기므로, 강제 저장을 해도 상대 작업은 복구 가능하다.
    """
    if not (author or "").strip():
        return SaveResult(ok=False, error="저장자를 입력해 주세요.")

    disk_mtime, disk_rev, disk_author = disk_stat()
    my_rev = int(data.get("rev", 0))
    if not force and disk_rev > my_rev:
        disk_updated = ""
        try:
            disk_updated = str(json.loads(pc.tree_path().read_text(encoding="utf-8")).get("updated_at", ""))
        except Exception:
            pass
        return SaveResult(ok=False, conflict=True, rev=my_rev, disk_rev=disk_rev,
                          disk_author=disk_author, disk_updated_at=disk_updated)

    snapshot(disk_author or "unknown")     # pre-image 보존 (없으면 None, 무해)
    # ★ 전체 교체(취합 반영·보관본·복원·강제 덮어쓰기)로 **사라지는 노드도 삭제로 기록**한다.
    #   안 하면 취합이 지운 빈 가지를 다른 사람이 고쳐 저장할 때 조용히 되살아난다
    #   (부분 병합은 rev 를 보지 않으므로 충돌 배너가 그 경로를 막아주지 못한다).
    #   디스크를 한 번 더 읽는 값이지만 저장은 사람이 누르는 조작이라 감당할 만하다.
    _prev_ids = _disk_node_ids()

    try:
        data = schema.normalize(data)
        data["rev"] = max(disk_rev, my_rev) + 1
        data["updated_at"] = schema.now_iso()
        data["updated_by"] = author
        save_json_atomic(data, pc.tree_path())
    except Exception as e:
        return SaveResult(ok=False, error=f"저장에 실패했습니다: {e}")

    _record_tombs(_prev_ids - {str(n.get("id")) for n in data["nodes"]}, data["rev"], author)
    audit({"author": author, "rev": data["rev"], "n_nodes": len(data["nodes"]),
           "action": "force" if (force and disk_rev > my_rev) else action})
    try:
        prune_history()
    except Exception:
        pass
    return SaveResult(ok=True, rev=data["rev"])


# ── 부분 저장 (동시 편집) ────────────────────────────────
# A가 편집하는 동안 B가 저장하면, 전체 교체 방식에서는 A의 [강제 덮어쓰기]가 **A가 만지지도 않은**
# B의 변경까지 옛 시점으로 되돌리고, [다시 읽기]는 A의 미저장 편집을 통째로 날린다. 둘 다 손실이다.
#
# 그래서 저장은 **델타**로 한다: 프론트가 "내가 만진 노드 id"(dirty)와 "내가 지운 id"(deleted)를
# 함께 보내면, 디스크 최신본을 base 로 삼아 **그 노드들만** 얹는다. 나머지는 손대지 않는다.
#
# ★ 이 설계가 견고한 이유 둘 —
#   ① base 를 `load_tree()` 로 읽어 **이미 정규화된**(order 0..n-1) 상태에서 시작한다.
#   ② 끝에서 `schema.normalize` 를 한 번 돈다 → 고아·사이클·level·order·AI 파생이 일괄 복구된다.
#   덕분에 병합은 **완벽한 트리를 만들 의무가 없고 그럴듯한 트리만** 만들면 된다.
# ★ `save_tree` 는 손대지 않는다 — "이 트리가 곧 진실이다"는 의미가 필요한 경로(force·restore·
#   취합/보관본 반영 뒤 저장)가 따로 있고, 기존 충돌 검사 회귀도 그 함수를 직접 부른다.


def _del_closure(deleted: list[str] | None, bmap: dict[str, dict]) -> set[str]:
    """삭제 id 의 **base 상 자손 폐포**.

    A가 지운 가지 아래에 B가 노드를 추가했으면 그 노드는 base 에 있고 A의 deleted 에는 없다.
    그대로 두면 부모가 사라져 `normalize` 의 고아 구제가 **ROOT 로 끌어올려 유령 lv3 부문**을 만든다.
    """
    seen: set[str] = set()
    stack = [d for d in (deleted or []) if d in bmap]
    kids: dict[str, list[str]] = {}
    for n in bmap.values():
        kids.setdefault(n.get("parent_id", ""), []).append(n["id"])
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        stack.extend(kids.get(i, []))
    return seen


def _dom_sig(domains: dict | None) -> str:
    """도메인 마스터의 안정 서명. JS 트윈(`_domSig`)과 **같은 규칙**이어야 한다.

    ★ `separators` 를 **반드시 명시**한다. `json.dumps` 기본값은 `", "` / `": "` (공백 포함)인데
    JS `JSON.stringify` 는 공백을 넣지 않는다. 빠뜨리면 서명이 **영원히 불일치**해
    도메인 변경이 매번 `dom_kept` 로 보류되고, 사용자는 이름을 바꿔도 목록이 안 바뀐다
    (노드 값만 `domRename` 으로 바뀌어 **도메인↔노드가 갈라진다**). 브라우저 검증에서 실제로 나왔다.
    `ensure_ascii=False` 도 같은 이유다 — JS 는 한글을 이스케이프하지 않는다.
    """
    d = domains or {}
    return json.dumps({k: list(d.get(k) or []) for k in sorted(d)},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rebase_order(out: list[dict], imap: dict[str, dict], touched: set[str]) -> None:
    """A가 순서를 바꾼 형제 그룹의 order 를 다시 매긴다 (제자리).

    A가 카드를 옮기면 `renumber` 가 형제 전체의 order 를 다시 매기는데 **그 형제들은 dirty 가 아니다**
    (JS 가 markDirty 를 안 한다). 그래서 순서는 프론트가 보고하는 게 아니라 여기서 유도한다 —
    액션마다 markDirty 를 흩뿌리면 새 액션이 생길 때 조용히 샌다.

    incoming 형제는 그 순서대로 `2i`, base 에만 있는 형제(그 사이 B가 추가한 것)는 바로 앞
    incoming 형제 뒤 `2i+1` 에 끼운다 → **A의 정렬 의도와 B의 삽입 위치를 동시에** 지킨다.
    절대값 정합성은 뒤따르는 `normalize` 의 renumber 가 보장하므로 여기선 상대 순서만 맞으면 된다.
    """
    if not touched:
        return
    by_parent: dict[str, list[dict]] = {}
    for n in out:
        by_parent.setdefault(n.get("parent_id", ""), []).append(n)
    for pid in touched:
        sibs = by_parent.get(pid) or []
        if not sibs:
            continue
        want = [n["id"] for n in sorted((x for x in sibs if x["id"] in imap),
                                        key=lambda x: imap[x["id"]].get("order", 0))]
        pos = {nid: 2 * i for i, nid in enumerate(want)}
        prev = -1
        for n in sorted(sibs, key=lambda x: x.get("order", 0)):     # base 순서로 훑으며
            if n["id"] in pos:
                prev = pos[n["id"]]
            else:
                prev += 1                                           # 앞 incoming 형제 바로 뒤
                pos[n["id"]] = prev
        for n in sibs:
            n["order"] = pos.get(n["id"], n.get("order", 0))


def save_merge(nodes: list[dict], domains: dict, dirty: list[str] | None,
               deleted: list[str] | None, author: str, my_rev: int = 0,
               dom_sig: str = "", revive: bool | None = None) -> SaveResult:
    """내가 만진 노드만 디스크 최신본에 얹어 저장한다 (동시 편집 보호).

    rev 충돌로 **거부하지 않는다** — 구성상 "안 만진 것은 안 건드린다"가 보장되므로 거부할 이유가 없다.
    같은 노드를 둘이 만졌으면 **내 값이 이긴다**(마지막 저장 승리). 상대 값은 pre-image 스냅샷에 남는다.

    ★ 단 하나 **사람에게 묻는 경우**가 있다: 내가 고친 업무를 그 사이 남이 **지웠을 때**.
      그냥 얹으면 남의 삭제가 조용히 무효가 되고(내 dirty 에 실려 되살아난다), 그냥 버리면
      내 수정이 조용히 사라진다. 어느 쪽이 옳은지는 데이터로 알 수 없으므로 `revive=None` 이면
      **아무것도 쓰지 않고** `revive_ask` 로 되돌려 화면에서 고르게 한다.
    """
    if not (author or "").strip():
        return SaveResult(ok=False, error="저장자를 입력해 주세요.")
    try:
        base, _ = load_tree()                       # ★ 정규화된 디스크 최신본 = 병합 기준
        disk_rev = int(base.get("rev", 0))
        bmap = {n["id"]: n for n in base.get("nodes", []) if isinstance(n, dict) and n.get("id")}
        imap = {n["id"]: n for n in (nodes or []) if isinstance(n, dict) and n.get("id")}
        # "dom"/"del" 같은 센티널 키는 imap 에 없으므로 자연히 걸러진다
        dirty_ids = {d for d in (dirty or []) if d in imap}
        del_ids = _del_closure(deleted, bmap)
        n_cascade = len(del_ids) - len([d for d in (deleted or []) if d in bmap])
        # 내가 고친 것 중 **base 에 없고 + 내 rev 이후에 지워진** 것 = 남이 지운 업무를 내가 고쳤다.
        # 묘비가 없으면 그냥 '새 업무'다 — 새 노드도 base 에 없으므로 이 구분이 반드시 필요하다.
        _tombs = load_tombs()
        gone = tuple(sorted(i for i in dirty_ids
                            if i not in bmap and int((_tombs.get(i) or {}).get("rev", -1)) > my_rev))
        n_revived = n_dropped = 0
        if gone:
            if revive is None:
                return SaveResult(ok=False, revive_ask=gone)      # ★ 아무것도 쓰지 않았다
            if revive:
                n_revived = len(gone)
            else:
                dirty_ids -= set(gone)                            # 삭제를 따른다 → 내 수정을 버린다
                n_dropped = len(gone)
        # 순서를 다시 매길 형제 그룹 — **옛 부모(base)** 를 반드시 포함한다.
        # A가 Y를 P1→P2 로 옮기면 incoming 에는 P2 만 있고, 구멍이 난 P1 은 dirty 어디에도 없다.
        touched = {imap[i].get("parent_id", "") for i in dirty_ids}
        touched |= {bmap[i].get("parent_id", "") for i in dirty_ids if i in bmap}
        touched |= {bmap[i].get("parent_id", "") for i in del_ids if i in bmap}
        # 남도 손댄 노드 — markDirty 가 찍어둔 updated_by 로 **공짜 탐지**. 차단하지 않고 알리기만.
        overlap = tuple(sorted(
            i for i in dirty_ids
            if i in bmap and (bmap[i].get("updated_by") or "") not in ("", author)
        )) if disk_rev > my_rev else ()

        out = [n for n in base.get("nodes", []) if n.get("id") not in del_ids and n.get("id") not in dirty_ids]
        out += [imap[i] for i in dirty_ids]
        _rebase_order(out, imap, touched)

        # 도메인 — dirty 에 "dom" 이 없으면 **base 유지**. 프론트는 자기(낡을 수 있는) 사본을 항상 보내므로
        # 그대로 채택하면 그 사이 B가 추가한 도메인 값이 조용히 사라진다.
        doms, dom_kept = base.get("domains", {}), False
        if "dom" in (dirty or []):
            if dom_sig and dom_sig != _dom_sig(base.get("domains")):
                dom_kept = True                     # 그 사이 남이 도메인을 고쳤다 → 내 변경은 보류
            else:
                doms = domains or {}

        snapshot(base.get("updated_by") or "unknown")       # pre-image — 되돌릴 길을 항상 남긴다
        data = schema.normalize({**base, "nodes": out, "domains": doms})
        data["rev"] = disk_rev + 1
        data["updated_at"] = schema.now_iso()
        data["updated_by"] = author
        save_json_atomic(data, pc.tree_path())
    except Exception as e:      # noqa: BLE001 — 저장 실패로 앱이 죽지 않게
        return SaveResult(ok=False, error=f"저장에 실패했습니다: {e}")

    _record_tombs(del_ids, data["rev"], author)
    audit({"author": author, "rev": data["rev"], "n_nodes": len(data["nodes"]), "action": "merge",
           "n_dirty": len(dirty_ids), "n_deleted": len(del_ids), "n_overlap": len(overlap),
           "n_revived": n_revived, "n_dropped": n_dropped})
    try:
        prune_history()
    except Exception:           # noqa: BLE001 — 정리 실패가 저장을 무르게 하지 않는다
        pass
    return SaveResult(ok=True, rev=data["rev"], merged=True, n_applied=len(dirty_ids),
                      n_deleted=len(del_ids), n_cascade=max(0, n_cascade),
                      overlap=overlap, dom_kept=dom_kept,
                      n_revived=n_revived, n_dropped=n_dropped)


def restore(name: str, author: str) -> tuple[SaveResult, dict | None]:
    """스냅샷을 새 저장(rev+1)으로 반영. blind copy 가 아니라 정식 저장 경로를 탄다 —
    복원 행위 자체도 스냅샷과 감사로그에 남는다."""
    snap = load_snapshot(name)
    if snap is None:
        return SaveResult(ok=False, error="스냅샷을 읽을 수 없습니다."), None
    _, disk_rev, _ = disk_stat()
    snap["rev"] = disk_rev              # 충돌 검사 통과용 — 의도된 되돌리기
    res = save_tree(snap, author, force=True, action="restore")
    return res, (snap if res.ok else None)
