from __future__ import annotations

import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def render(value: str, variables: dict[str, str]) -> str:
    def substitute(match):
        name, operator, fallback = match.groups()
        value = variables.get(name, "")
        if operator == "?" and not value:
            raise ValueError(f"Missing {name}")
        return value or fallback

    return re.sub(r"\$\{([A-Z_]+):([?-])([^}]*)\}", substitute, value)


@pytest.mark.parametrize("service,port_name", [("engine", "ENGINE_PORT"), ("userui", "USERUI_PORT")])
def test_compose_listener_mapping_and_connection_follow_configured_ports(service, port_name):
    variables = {"ENGINE_API_KEY": "test-key", "ENGINE_PORT": "9123", "USERUI_PORT": "9124", "ENGINE_URL": "https://remote.example"}
    config = COMPOSE["services"][service]
    environment = {name: render(str(value), variables) for name, value in config["environment"].items()}
    port = variables[port_name]
    assert environment[port_name] == port
    assert render(config["ports"][0], variables) == f"127.0.0.1:{port}:{port}"
    assert environment["ENGINE_URL"] == "http://engine:9123"
    assert environment["ENGINE_HOST" if service == "engine" else "USERUI_HOST"] == "0.0.0.0"


@pytest.mark.parametrize("service,port_name", [("engine", "ENGINE_PORT"), ("userui", "USERUI_PORT")])
def test_compose_healthcheck_uses_actual_listener_port(service, port_name):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        test = COMPOSE["services"][service]["healthcheck"]["test"]
        command = test[1:]
        if service == "engine":
            command = [sys.executable, *command[1:]]
        result = subprocess.run(
            command,
            env={**os.environ, port_name: str(server.server_port)},
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert requests == ["/health"]
    finally:
        server.shutdown()
        server.server_close()
        serving.join()
