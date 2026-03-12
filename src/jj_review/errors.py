"""User-facing error types shared across CLI commands."""

from __future__ import annotations


class CliError(RuntimeError):
    """Base error for user-facing CLI failures."""

    exit_code = 1


class CommandNotImplementedError(CliError):
    """Raised for stubbed commands that are not implemented yet."""

    exit_code = 2

    def __init__(self, command: str) -> None:
        super().__init__(f"`{command}` is not implemented yet.")
