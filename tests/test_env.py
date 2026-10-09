import os

from trader_agent.env import load_env


def test_only_explicit_env_file_is_loaded_with_environment_priority(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ENGINE_PORT=8123\n")
    child = tmp_path / "child"
    child.mkdir()
    chosen = child / "selected.env"
    chosen.write_text("ENGINE_PORT=9123\nENGINE_HOST=127.0.0.1\n")
    monkeypatch.delenv("ENGINE_PORT", raising=False)
    monkeypatch.setenv("ENGINE_HOST", "0.0.0.0")
    monkeypatch.chdir(tmp_path)

    load_env(chosen)

    assert os.environ["ENGINE_PORT"] == "9123"
    assert os.environ["ENGINE_HOST"] == "0.0.0.0"


def test_default_env_file_is_in_working_directory_without_parent_search(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ENGINE_PORT=8123\n")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.delenv("ENGINE_PORT", raising=False)
    monkeypatch.chdir(child)

    load_env()

    assert "ENGINE_PORT" not in os.environ


def test_relative_directories_are_resolved_from_selected_env_file(tmp_path, monkeypatch):
    chosen = tmp_path / "settings.env"
    chosen.write_text(
        "TRADER_CONFIGS_DIR=./configs\nTRADER_DATA_DIR=./data\nTRADER_LOGS_DIR=./logs\n"
    )
    for name in ("TRADER_CONFIGS_DIR", "TRADER_DATA_DIR", "TRADER_LOGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path.parent)

    load_env(chosen)

    assert os.environ["TRADER_CONFIGS_DIR"] == str(tmp_path / "configs")
    assert os.environ["TRADER_DATA_DIR"] == str(tmp_path / "data")
    assert os.environ["TRADER_LOGS_DIR"] == str(tmp_path / "logs")
