"""Persistent browser profile helpers for CLI commands."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scout_pilot.config import AppConfig


_PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class BrowserProfileInfo:
    """User-facing persistent profile diagnostics."""

    name: str
    path: Path
    exists: bool
    git_ignored: bool | None


def resolve_profile_path(config: AppConfig, profile: str = "default") -> Path:
    """Resolve a profile name to a local persistent browser profile path."""

    normalized = profile.strip() or "default"
    if normalized == "default":
        return config.browser_profile_dir
    if not _PROFILE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "profile name must use letters, digits, dot, dash or underscore"
        )
    return Path(".browser-profiles") / normalized


def inspect_browser_profile(
    config: AppConfig,
    *,
    profile: str = "default",
    cwd: Path | None = None,
) -> BrowserProfileInfo:
    """Return path, existence and Git ignore status for a browser profile."""

    path = resolve_profile_path(config, profile)
    return BrowserProfileInfo(
        name=profile.strip() or "default",
        path=path,
        exists=_absolute(path, cwd or Path.cwd()).exists(),
        git_ignored=is_ignored_by_git(path, cwd=cwd),
    )


def is_ignored_by_git(path: Path, *, cwd: Path | None = None) -> bool | None:
    """Return whether Git ignores path, or None when it cannot be checked."""

    working_dir = cwd or Path.cwd()
    root = _git_root(working_dir)
    if root is None:
        return None

    absolute_path = _absolute(path, working_dir)
    try:
        relative_path = absolute_path.relative_to(root)
    except ValueError:
        return None

    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative_path.as_posix()],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _git_root(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def _absolute(path: Path, cwd: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (cwd / path).resolve()
