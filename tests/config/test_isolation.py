import os
import subprocess
import sys
from pathlib import Path


def test_config_imports_without_loading_engine():
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sys",
            "import trader_agent.config.base",
            "import trader_agent.config.llm",
            "import trader_agent.config.market",
            "import trader_agent.config.news",
            "assert not any(name.startswith('trader_agent.engine') for name in sys.modules)",
        ])],
        env={**os.environ, "PYTHONPATH": str(source)},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
