"""Runtime configuration helpers shared by CLI entry points.

Keep credentials in environment variables instead of source code and validate
user-controlled filesystem identifiers before using them in paths.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Tuple

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def get_api_credentials() -> Tuple[int, str]:
    """Read and validate Telegram API credentials from the environment."""
    raw_api_id = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()

    if not raw_api_id:
        raise RuntimeError("未配置 TG_API_ID")
    if not api_hash:
        raise RuntimeError("未配置 TG_API_HASH")

    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise RuntimeError("TG_API_ID 必须是整数") from exc

    if api_id <= 0:
        raise RuntimeError("TG_API_ID 必须大于 0")
    if len(api_hash) < 16:
        raise RuntimeError("TG_API_HASH 格式无效")

    return api_id, api_hash


def validate_session_name(session_name: str) -> str:
    """Allow only a single safe session identifier component."""
    if not SESSION_NAME_RE.fullmatch(session_name):
        raise ValueError("Session 名称仅允许字母、数字、下划线和短横线，长度 1-64")
    return session_name


def get_session_path(base_dir: str | Path, session_name: str) -> Path:
    """Return a validated session path below ``base_dir``."""
    safe_name = validate_session_name(session_name.strip())
    base = Path(base_dir).resolve()
    path = (base / safe_name).resolve()
    if base != path.parent:
        raise ValueError("Session 路径越界")
    return path
