from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

import trader_agent.client as client_module
import trader_agent.main as entrypoint


@pytest.fixture
def job_requests(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        result = ({"job_id": "test-job", "status": "empty", "written_digests": 0}
                  if request.url.path.endswith("daily-digest") else {"THYAO": True})
        return httpx.Response(200, json=result)

    client_class = client_module.EngineClient
    monkeypatch.setattr(
        client_module, "EngineClient",
        lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setenv("ENGINE_URL", "https://engine.example")
    monkeypatch.setenv("ENGINE_API_KEY", "maintenance-key")
    monkeypatch.setattr(entrypoint, "load_env", lambda path: None)
    monkeypatch.setattr(entrypoint, "setup_logging", lambda directory: None)
    return requests


@pytest.mark.parametrize("command, path, options", [
    ("prefetch-market", "/v1/jobs/prefetch-market", []),
    ("daily-digest", "/v1/jobs/daily-digest", []),
    ("daily-digest", "/v1/jobs/daily-digest", ["--since=24h"]),
    ("daily-digest", "/v1/jobs/daily-digest", ["--since=2026-10-01"]),
])
def test_maintenance_commands_send_authenticated_jobs(
    monkeypatch, capsys, job_requests, command, path, options,
):
    monkeypatch.setattr(sys, "argv", ["trader-agent", command, *options])
    before = datetime.now(timezone.utc)
    entrypoint.main()
    after = datetime.now(timezone.utc)

    expected = ({"job_id": "test-job", "status": "empty", "written_digests": 0}
                if command == "daily-digest" else {"THYAO": True})
    assert json.loads(capsys.readouterr().out) == expected
    assert len(job_requests) == 1
    request = job_requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://engine.example" + path
    assert request.headers["Authorization"] == "Bearer maintenance-key"
    if command == "daily-digest":
        since = json.loads(request.content)["since"]
        if options == ["--since=24h"]:
            assert before - timedelta(hours=24) <= datetime.fromisoformat(since) <= (
                after - timedelta(hours=24)
            )
        elif options:
            assert since == "2026-10-01T00:00:00+00:00"
        else:
            assert since is None


def test_invalid_since_fails_before_sending_job(monkeypatch, job_requests):
    monkeypatch.setattr(sys, "argv", ["trader-agent", "daily-digest", "--since=bad-date"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert not job_requests


@pytest.mark.parametrize("url", [None, ""])
def test_maintenance_requires_engine_url(monkeypatch, job_requests, url):
    if url is None:
        monkeypatch.delenv("ENGINE_URL")
    else:
        monkeypatch.setenv("ENGINE_URL", url)
    monkeypatch.setenv("ENGINE_HOST", "127.0.0.1")
    monkeypatch.setenv("ENGINE_PORT", "9123")
    monkeypatch.setattr(sys, "argv", ["trader-agent", "prefetch-market"])

    with pytest.raises(RuntimeError, match="ENGINE_URL zorunlu"):
        entrypoint.main()
    assert not job_requests


def test_engine_loads_environment_once_before_creating_app(monkeypatch):
    import uvicorn
    from trader_agent.engine import api

    def read_environment(path, *, override):
        monkeypatch.setenv("ENGINE_HOST", "127.0.0.1")
        monkeypatch.setenv("ENGINE_PORT", "8123")
        monkeypatch.setenv("ENGINE_API_KEY", "test-key")

    load_dotenv = Mock(side_effect=read_environment)
    create_app = Mock(wraps=api.create_app)
    run = Mock()
    monkeypatch.setattr("trader_agent.env.load_dotenv", load_dotenv)
    monkeypatch.setattr(api, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(entrypoint, "setup_logging", lambda directory: None)
    monkeypatch.setattr(sys, "argv", ["trader-agent", "engine"])

    entrypoint.main()

    load_dotenv.assert_called_once_with(Path.cwd() / ".env", override=False)
    create_app.assert_called_once_with(api_key="test-key")
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8123}
    assert run.call_args.args[0].title == "trader-agent engine"


@pytest.mark.parametrize("listening_env", [True, False])
def test_engine_cli_overrides_environment_without_changing_client_url(monkeypatch, listening_env):
    import uvicorn
    from trader_agent.engine import api

    if listening_env:
        monkeypatch.setenv("ENGINE_HOST", "127.0.0.1")
        monkeypatch.setenv("ENGINE_PORT", "8123")
    else:
        monkeypatch.delenv("ENGINE_HOST", raising=False)
        monkeypatch.delenv("ENGINE_PORT", raising=False)
    monkeypatch.setenv("ENGINE_URL", "https://remote-engine.example")
    monkeypatch.setenv("ENGINE_API_KEY", "override-key")
    monkeypatch.setattr(entrypoint, "load_env", lambda path: None)
    monkeypatch.setattr(entrypoint, "setup_logging", lambda directory: None)
    create_app = Mock()
    run = Mock()
    monkeypatch.setattr(api, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["trader-agent", "engine", "--host", "0.0.0.0", "--port", "9123"])

    entrypoint.main()

    create_app.assert_called_once_with(api_key="override-key")
    assert run.call_args.kwargs == {"host": "0.0.0.0", "port": 9123}
    assert os.environ["ENGINE_URL"] == "https://remote-engine.example"


@pytest.mark.parametrize("name", ["ENGINE_HOST", "ENGINE_PORT"])
def test_engine_requires_listening_settings(monkeypatch, name):
    import uvicorn
    from trader_agent.engine import api

    monkeypatch.setenv("ENGINE_HOST", "127.0.0.1")
    monkeypatch.setenv("ENGINE_PORT", "9123")
    monkeypatch.delenv(name)
    monkeypatch.setattr(entrypoint, "load_env", lambda path: None)
    monkeypatch.setattr(entrypoint, "setup_logging", lambda directory: None)
    create_app = Mock()
    run = Mock()
    monkeypatch.setattr(api, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["trader-agent", "engine"])

    with pytest.raises(KeyError, match=name):
        entrypoint.main()
    create_app.assert_not_called()
    run.assert_not_called()


def test_explicit_env_file_resolves_configs_and_logs_from_its_directory(tmp_path, monkeypatch):
    import uvicorn
    from trader_agent.config.llm import LLMConfig
    from trader_agent.engine import api

    settings = tmp_path / "settings"
    settings.mkdir()
    configs = settings / "configs"
    configs.mkdir()
    (configs / "llm.yaml").write_text("agent_model: custom-agent\n")
    env_file = settings / ".env"
    env_file.write_text(
        "ENGINE_HOST=127.0.0.1\nENGINE_PORT=9123\n"
        "TRADER_CONFIGS_DIR=./configs\nTRADER_LOGS_DIR=./logs\n"
    )
    for name in ("ENGINE_HOST", "ENGINE_PORT", "TRADER_CONFIGS_DIR", "TRADER_LOGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api, "create_app", Mock())
    run = Mock()
    logging = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(entrypoint, "setup_logging", logging)
    monkeypatch.setattr(sys, "argv", ["trader-agent", "--env-file", str(env_file), "engine"])

    entrypoint.main()

    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 9123}
    logging.assert_called_once_with(settings / "logs")
    assert LLMConfig.load().agent_model == "custom-agent"
