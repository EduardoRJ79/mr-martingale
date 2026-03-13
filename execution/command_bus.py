"""
File-based command bus for local console -> running bot control.

Queue layout:
  execution/commands/pending/*.json
  execution/commands/processed/*.json

This avoids shared-memory coupling between Streamlit UI and the running bot process.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import config as cfg
except ImportError:
    import config as cfg


COMMAND_ROOT = Path(__file__).parent / "commands"
PENDING_DIR = COMMAND_ROOT / "pending"
PROCESSED_DIR = COMMAND_ROOT / "processed"


@dataclass
class Command:
    id: str
    ts: str
    action: str
    source: str
    payload: Dict[str, Any]


VALID_ACTIONS = {
    "manual_long",
    "manual_short",
    "manual_close",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(cmd_id: str) -> str:
    return "".join(ch for ch in cmd_id if ch.isalnum() or ch in ("-", "_"))


def enqueue(action: str, source: str = "console", payload: Optional[Dict[str, Any]] = None) -> Path:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid command action: {action}")

    ensure_dirs()
    cmd = Command(
        id=str(uuid.uuid4()),
        ts=now_iso(),
        action=action,
        source=source,
        payload=payload or {},
    )

    # filename sortable by timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    fname = f"{ts}_{_safe_name(cmd.id)}.json"
    path = PENDING_DIR / fname
    path.write_text(json.dumps(asdict(cmd), indent=2))
    return path


def list_pending() -> List[Tuple[Path, Dict[str, Any]]]:
    ensure_dirs()
    rows: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            rows.append((path, data))
        except Exception:
            # keep malformed files from blocking queue
            rows.append((path, {
                "id": path.stem,
                "ts": now_iso(),
                "action": "invalid",
                "source": "unknown",
                "payload": {},
            }))
    return rows


def mark_processed(path: Path, cmd: Dict[str, Any], status: str, message: str = "") -> Path:
    ensure_dirs()
    out = dict(cmd)
    out["processed_at"] = now_iso()
    out["status"] = status
    out["message"] = message

    base = path.stem
    out_path = PROCESSED_DIR / f"{base}_{status}.json"
    out_path.write_text(json.dumps(out, indent=2))

    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass

    return out_path


def recent_processed(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_dirs()
    files = sorted(PROCESSED_DIR.glob("*.json"), reverse=True)[:limit]
    out: List[Dict[str, Any]] = []
    for p in files:
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out
