from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("VERITY_DISABLE_EXEC", "1")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".verity").mkdir()
    (tmp_path / ".verity" / "config.yaml").write_text(
        "version: 1\nproject: test-project\nverification:\n  allow_exec: false\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "import os\n\n\ndef authenticate_user(token: str) -> dict:\n    return {}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "from app import authenticate_user\n\n\ndef test_ok():\n    assert callable(authenticate_user)\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test-project"\nversion = "0.1.0"\n'
        'dependencies = ["pyjwt>=2.8", "pytest>=8"]\n\n'
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\npythonpath = [\".\"]\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def engine(project: Path):
    from verity.engine import Engine

    eng = Engine(project)
    yield eng
    eng.repo.close()
