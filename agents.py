"""Agent registry for multi-agent isolated workspaces.

Each agent gets a server-issued id and an isolated workspace under:
  - output/agents/<id>/      (section_a/b/c)
  - staging/agents/<id>/     (state.json, lock files, prompt files)
  - input/agents/<id>/       (request.json)
  - mini/staging/agents/<id>/

Registry is persisted to staging/agents/registry.json with a sliding 24h TTL.
Every successful tool call extends the agent's expiry; expired agents are
swept lazily on each register_agent call.
"""
from __future__ import annotations

import fcntl
import json
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEFAULT_TTL_HOURS = 24
_ID_PREFIX = "a-"
_ID_BYTES = 4  # -> 8 hex chars


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def new_agent_id() -> str:
    return f"{_ID_PREFIX}{secrets.token_hex(_ID_BYTES)}"


class _RegLock:
    """fcntl-based exclusive lock for the registry file."""

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._fh = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


class AgentRegistry:
    """File-backed registry with per-agent isolated workspace paths."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.registry_path = self.base_dir / "staging" / "agents" / "registry.json"
        self.lock_path = self.base_dir / "staging" / "agents" / "registry.lock"
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    # ── path helpers ────────────────────────────────────────────────────

    def output_dir(self, agent_id: str) -> Path:
        return self.base_dir / "output" / "agents" / agent_id

    def staging_dir(self, agent_id: str) -> Path:
        return self.base_dir / "staging" / "agents" / agent_id

    def input_dir(self, agent_id: str) -> Path:
        return self.base_dir / "input" / "agents" / agent_id

    def mini_staging_dir(self, agent_id: str) -> Path:
        return self.base_dir / "mini" / "staging" / "agents" / agent_id

    # ── registry I/O ────────────────────────────────────────────────────

    def _read(self) -> dict:
        if not self.registry_path.exists():
            return {"agents": {}}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {"agents": {}}

    def _write(self, data: dict) -> None:
        self.registry_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── public API ──────────────────────────────────────────────────────

    def register(self, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict:
        """Issue a new agent id, create its workspace, sweep expired ones."""
        ttl_hours = max(1, int(ttl_hours))
        with _RegLock(self.lock_path):
            data = self._read()
            self._sweep_expired_locked(data)
            agent_id = new_agent_id()
            while agent_id in data["agents"]:
                agent_id = new_agent_id()
            now = _utcnow()
            meta = {
                "created_at": _iso(now),
                "last_active_at": _iso(now),
                "expires_at": _iso(now + timedelta(hours=ttl_hours)),
                "ttl_hours": ttl_hours,
            }
            data["agents"][agent_id] = meta
            self._write(data)
            self._ensure_workspace(agent_id)
        return {"agent_id": agent_id, **meta}

    def touch(self, agent_id: str) -> bool:
        """Slide the expiry forward. Returns False if unknown or already expired."""
        if not agent_id:
            return False
        with _RegLock(self.lock_path):
            data = self._read()
            meta = data["agents"].get(agent_id)
            if not meta:
                return False
            exp = _parse_iso(meta.get("expires_at", ""))
            now = _utcnow()
            if exp is None or exp < now:
                # Expired — purge and reject.
                data["agents"].pop(agent_id, None)
                self._write(data)
                self._delete_workspace(agent_id)
                return False
            ttl = int(meta.get("ttl_hours", DEFAULT_TTL_HOURS))
            meta["last_active_at"] = _iso(now)
            meta["expires_at"] = _iso(now + timedelta(hours=ttl))
            data["agents"][agent_id] = meta
            self._write(data)
            return True

    def release(self, agent_id: str) -> bool:
        """Drop the agent immediately and wipe its workspace."""
        if not agent_id:
            return False
        with _RegLock(self.lock_path):
            data = self._read()
            if agent_id not in data["agents"]:
                return False
            data["agents"].pop(agent_id)
            self._write(data)
        self._delete_workspace(agent_id)
        return True

    def get(self, agent_id: str) -> Optional[dict]:
        if not agent_id:
            return None
        return self._read()["agents"].get(agent_id)

    def list_all(self) -> dict:
        """Return a snapshot of all known agents (read-only copy)."""
        return dict(self._read()["agents"])

    def cleanup_expired(self) -> list[str]:
        """Force a sweep. Returns the ids that were removed."""
        with _RegLock(self.lock_path):
            data = self._read()
            removed = self._sweep_expired_locked(data)
            self._write(data)
        for aid in removed:
            self._delete_workspace(aid)
        return removed

    # ── internals ───────────────────────────────────────────────────────

    def _sweep_expired_locked(self, data: dict) -> list[str]:
        now = _utcnow()
        removed: list[str] = []
        for aid, meta in list(data["agents"].items()):
            exp = _parse_iso(meta.get("expires_at", ""))
            if exp is None or exp < now:
                removed.append(aid)
                data["agents"].pop(aid, None)
        return removed

    def _ensure_workspace(self, agent_id: str) -> None:
        for sub in (
            self.output_dir(agent_id),
            self.staging_dir(agent_id),
            self.input_dir(agent_id),
            self.mini_staging_dir(agent_id),
        ):
            sub.mkdir(parents=True, exist_ok=True)

    def _delete_workspace(self, agent_id: str) -> None:
        for sub in (
            self.output_dir(agent_id),
            self.staging_dir(agent_id),
            self.input_dir(agent_id),
            self.mini_staging_dir(agent_id),
        ):
            if sub.exists():
                shutil.rmtree(sub, ignore_errors=True)
