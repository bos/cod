import importlib.util
import subprocess
from pathlib import Path


def _load_check_module():
    module_path = Path(__file__).resolve().parents[2] / "check.py"
    spec = importlib.util.spec_from_file_location("repo_check", module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_script = _load_check_module()


def test_ensure_project_environment_syncs_even_when_venv_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    venv_python = tmp_path / ".venv" / check_script._venv_python_relative_path()
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(check_script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_script, "VENV_PYTHON", venv_python)
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/already-active")

    commands: list[tuple[str, ...]] = []
    cwd_values: list[Path] = []
    env_values: list[dict[str, str]] = []

    def fake_run(
        command: tuple[str, ...],
        *,
        check: bool,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        commands.append(command)
        cwd_values.append(cwd)
        env_values.append(env)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(check_script.subprocess, "run", fake_run)

    check_script.ensure_project_environment()

    assert commands == [("uv", "sync", "--locked")]
    assert cwd_values == [tmp_path]
    assert len(env_values) == 1
    assert "VIRTUAL_ENV" not in env_values[0]
