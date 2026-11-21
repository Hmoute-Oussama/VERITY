from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_public_integration_matrix(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "run_matrix.py")],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "fastapi-auth" in result.stdout
    assert "contradicted-change" in result.stdout
    assert "stale-evidence" in result.stdout
