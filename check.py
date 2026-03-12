#!/usr/bin/env python3
"""Run the standard local verification checks for this repository."""

from __future__ import annotations

import os
import shlex
import subprocess
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path


def _venv_python_relative_path() -> Path:
    if os.name == "nt":
        return Path("Scripts/python.exe")
    return Path("bin/python")


REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / ".venv" / _venv_python_relative_path()
CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff", ("-m", "ruff", "check")),
    ("ty", ("-m", "ty", "check")),
    ("pytest", ("-m", "pytest")),
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run Ruff, type checking, and the test suite in sequence."""

    parser = ArgumentParser(
        prog="check.py",
        description="Run the local Ruff, ty, and pytest checks.",
    )
    parser.parse_args(argv)
    ensure_project_environment()

    for name, command in CHECKS:
        full_command = (str(VENV_PYTHON), *command)
        print(f"==> {name}: {shlex.join(full_command)}", flush=True)
        completed = subprocess.run(
            full_command,
            check=False,
            cwd=REPO_ROOT,
        )
        if completed.returncode != 0:
            return completed.returncode

    return 0


def ensure_project_environment() -> None:
    """Refresh the project virtualenv before running the verification suite."""

    sync_command = ("uv", "sync", "--locked")
    print(f"==> bootstrap: {shlex.join(sync_command)}", flush=True)
    completed = subprocess.run(
        sync_command,
        check=False,
        cwd=REPO_ROOT,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
