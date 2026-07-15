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


def load_tree() -> tuple[dict, list[str]]:
    """정본 트리 로드. (data, 경고메시지목록).

    파일이 없으면 bootstrap, 손상되면 손상본을 보존한 채 bootstrap 으로 폴백한다.
    어떤 경우에도 앱이 죽지 않는다 (공통규칙 5).
    """
    warns: list[str] = []
    p = pc.tree_path()
    if not p.exists():
        return schema.bootstrap(), warns
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
        out.append({"file": f.name, "ts": ts, "author": author, "rev": rev, "n_nodes": n_nodes})
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


def prune_history(keep_days: int = 90, keep_min: int = 50) -> int:
    """오래된 스냅샷 정리. 최근 keep_days 일 전량 + 최신 keep_min 개는 항상 보존."""
    try:
        files = sorted(pc.get_history_dir().glob("process_tree_*.json"), reverse=True)
    except Exception:
        return 0
    if len(files) <= keep_min:
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in files[keep_min:]:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            continue
    return removed


# ── 저장 (충돌 검사 포함) ───────────────────────────────

def save_tree(data: dict, author: str, force: bool = False, action: str = "save") -> SaveResult:
    """트리 저장. 디스크 rev 가 내 rev 보다 크면 conflict 로 거부(force 로 덮어쓰기).

    덮어쓰기 직전 디스크본을 스냅샷으로 남기므로, 강제 저장을 해도 상대 작업은 복구 가능하다.
    """
    if not (author or "").strip():
        return SaveResult(ok=False, error="작성자를 입력해 주세요.")

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

    try:
        data = schema.normalize(data)
        data["rev"] = max(disk_rev, my_rev) + 1
        data["updated_at"] = schema.now_iso()
        data["updated_by"] = author
        save_json_atomic(data, pc.tree_path())
    except Exception as e:
        return SaveResult(ok=False, error=f"저장에 실패했습니다: {e}")

    audit({"author": author, "rev": data["rev"], "n_nodes": len(data["nodes"]),
           "action": "force" if (force and disk_rev > my_rev) else action})
    try:
        prune_history()
    except Exception:
        pass
    return SaveResult(ok=True, rev=data["rev"])


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
