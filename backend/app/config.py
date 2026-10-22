"""Central configuration for Atlas backend.

All secrets stay server-side. Nothing here is ever sent to the browser
except non-sensitive status flags (e.g. whether a key is configured).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
DATA_DIR = Path(os.environ.get("ATLAS_DATA_DIR", BASE_DIR / "data"))
WORKSPACES_DIR = Path(os.environ.get("ATLAS_WORKSPACES_DIR", BASE_DIR / "workspaces"))
DB_PATH = Path(os.environ.get("ATLAS_DB_PATH", DATA_DIR / "atlas.db"))

def _read_hermes_go_key() -> str:
    """User-approved fallback: read OPENCODE_GO_API_KEY from the local
    Hermes agent's .env (server-side only, never logged or sent to clients).
    Explicit OPENCODE_ZEN_API_KEY env var always takes precedence."""
    try:
        from pathlib import Path as _P

        hermes_env = _P.home() / "AppData" / "Local" / "hermes" / ".env"
        if not hermes_env.exists():
            return ""
        for line in hermes_env.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() == "OPENCODE_GO_API_KEY":
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


OPENCODE_ZEN_API_KEY = os.environ.get("OPENCODE_ZEN_API_KEY", "").strip() or _read_hermes_go_key()
KEY_SOURCE = "env" if os.environ.get("OPENCODE_ZEN_API_KEY", "").strip() else (
    "hermes-env" if OPENCODE_ZEN_API_KEY else "none")
OPENCODE_GO_BASE = os.environ.get(
    "OPENCODE_GO_BASE", "https://opencode.ai/inference/go/v1"
).rstrip("/")
OPENCODE_GO_MODELS_URL = f"{OPENCODE_GO_BASE}/models"

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434").rstrip("/")

DEFAULT_MODEL = os.environ.get("ATLAS_DEFAULT_MODEL", "opencode-go/glm-5.3-flash")
CATALOG_TTL_SECONDS = int(os.environ.get("ATLAS_CATALOG_TTL", "3600"))
CATALOG_TIMEOUT_SECONDS = float(os.environ.get("ATLAS_CATALOG_TIMEOUT", "20"))

MAX_CONCURRENT_EXECUTIONS = int(os.environ.get("ATLAS_MAX_CONCURRENT", "4"))
MAX_STEPS_PER_EXECUTION = int(os.environ.get("ATLAS_MAX_STEPS", "60"))
MAX_TOOL_OUTPUT_CHARS = int(os.environ.get("ATLAS_MAX_TOOL_OUTPUT", "20000"))
EXECUTION_TIMEOUT_SECONDS = int(os.environ.get("ATLAS_EXEC_TIMEOUT", "1800"))
SHELL_TIMEOUT_SECONDS = int(os.environ.get("ATLAS_SHELL_TIMEOUT", "120"))

AUTO_APPROVE_WORKSPACE_WRITES = os.environ.get("ATLAS_AUTO_APPROVE", "1") == "1"
ALLOW_NETWORK = os.environ.get("ATLAS_ALLOW_NETWORK", "1") == "1"

SERVER_PASSWORD = os.environ.get("ATLAS_SERVER_PASSWORD", "")
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("ATLAS_CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]


@dataclass
class Settings:
    default_model: str = DEFAULT_MODEL
    catalog_ttl_seconds: int = CATALOG_TTL_SECONDS
    auto_approve_workspace_writes: bool = AUTO_APPROVE_WORKSPACE_WRITES
    max_steps: int = MAX_STEPS_PER_EXECUTION


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
