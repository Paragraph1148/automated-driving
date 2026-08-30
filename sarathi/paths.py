"""Locating scenarios and viewer assets, wherever the package is running from.

The demo has to work three ways: from a git checkout during development, from an
installed wheel (``uvx --from git+... sarathi serve``), and from a directory that
is not the repository root. Resolving by walking up from ``__file__`` or by
trusting the working directory breaks at least one of those, so both lookups are
centralised here with an explicit search order.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
#: Scenarios bundled into the wheel by the build backend.
BUNDLED_SCENARIOS = PACKAGE_ROOT / "_scenarios"
ASSETS = PACKAGE_ROOT / "assets"


def scenario_dir(override: str | Path | None = None) -> Path:
    """Directory holding scenario YAML files.

    A checkout's own ``scenarios/`` wins over the bundled copy, so editing a
    scenario and re-running picks the edit up without reinstalling anything.
    """
    if override is not None:
        path = Path(override)
        if not path.is_dir():
            raise FileNotFoundError(f"no scenario directory at {path}")
        return path
    local = Path.cwd() / "scenarios"
    if local.is_dir() and any(local.glob("*.yaml")):
        return local
    if BUNDLED_SCENARIOS.is_dir():
        return BUNDLED_SCENARIOS
    raise FileNotFoundError(
        "no scenarios found - run from the repository root, or pass --scenarios")


def viewer_template() -> str:
    """The Mission Control HTML, shared by the live server and the replay build."""
    return (ASSETS / "mission_control.html").read_text(encoding="utf-8")
