from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_maintenance_imports_without_loading_engine_or_model_sdk():
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sys",
            "import trader_agent.main",
            "import trader_agent.client",
            "assert not any(name.startswith('trader_agent.engine') for name in sys.modules)",
            "assert 'anthropic' not in sys.modules",
            "assert 'uvicorn' not in sys.modules",
        ])],
        env={**os.environ, "PYTHONPATH": str(source)},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
