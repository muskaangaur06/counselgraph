"""Centralized environment configuration. Call load_env() once early so other modules can just read os.environ."""

from __future__ import annotations

import os
from dotenv import load_dotenv

_loaded = False


def load_env(dotenv_path: str | None = None) -> None:
    global _loaded
    if not _loaded:
        load_dotenv(dotenv_path)
        _loaded = True


def require_env(name: str) -> str:
    """Fetch a required env var, failing fast with a clear message if missing."""
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Add it to your .env file (see .env.example) or export it directly."
        )
    return value


load_env()
