import pytest


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_CONFIGS_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRADER_LOGS_DIR", str(tmp_path / "logs"))
    return tmp_path
