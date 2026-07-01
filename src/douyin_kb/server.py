from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import load_config
from .inbox import append_inbox


def run_server(config_path: Path, host: str, port: int, token: str = "") -> None:
    config = load_config(config_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/ingest":
                self._send_json(404, {"error": "not_found"})
                return

            if token:
                expected = f"Bearer {token}"
                if self.headers.get("Authorization", "") != expected:
                    self._send_json(401, {"error": "unauthorized"})
                    return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            try:
                data: Any = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid_json"})
                return

            if not isinstance(data, dict):
                self._send_json(400, {"error": "expected_object"})
                return

            append_inbox(config.inbox_jsonl, data)
            self._send_json(200, {"ok": True})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port}/ingest")
    server.serve_forever()

