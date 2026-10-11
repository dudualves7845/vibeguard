"""HTTP Web Server for the VibeGuard Dashboard.

Zero external dependencies: built entirely on Python's standard library.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import hmac
import os
from pathlib import Path
import socketserver
import secrets
import sys
import threading
import time
from typing import Any, Dict, Optional
import urllib.parse
import webbrowser
from collections import OrderedDict, deque

from .ui import VibeGuardService


ASSETS_DIR = Path(__file__).parent / "ui_assets"


class RateLimiter:
    """Thread-safe sliding-window limiter with bounded per-client state."""

    def __init__(self, max_requests: int = 100, window_secs: int = 60, max_clients: int = 4096):
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        if isinstance(window_secs, bool) or not isinstance(window_secs, (int, float)) or window_secs <= 0:
            raise ValueError("window_secs must be a positive number")
        if type(max_clients) is not int or max_clients < 1:
            raise ValueError("max_clients must be a positive integer")
        self.max_requests = max_requests
        self.window_secs = window_secs
        self.max_clients = max_clients
        self.requests = OrderedDict()
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """Check if client is within rate limit. Returns True if allowed, False if throttled."""
        now = time.monotonic()
        cutoff = now - self.window_secs

        with self._lock:
            client_requests = self.requests.pop(client_id, deque())
            while client_requests and client_requests[0] <= cutoff:
                client_requests.popleft()

            allowed = len(client_requests) < self.max_requests
            if allowed:
                client_requests.append(now)
            if client_requests:
                self.requests[client_id] = client_requests
                if len(self.requests) > self.max_clients:
                    self.requests.popitem(last=False)
            return allowed


class VibeGuardRequestHandler(BaseHTTPRequestHandler):
    """Handles REST API and static asset requests for the dashboard."""

    server_version = "VibeGuardServer/1.0"

    @property
    def service(self) -> VibeGuardService:
        return getattr(self.server, "service", None)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy access logs unless DEBUG environment variable is set
        if os.environ.get("VIBEGUARD_DEBUG"):
            super().log_message(format, *args)

    def do_OPTIONS(self) -> None:
        if not self._valid_origin_host():
            self._send_error_json("Origin or Host not allowed", status=403)
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._valid_origin_host():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-VibeGuard-Token")

    def _valid_origin_host(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed_host = urllib.parse.urlsplit("http://" + host)
            allowed = self.server.allowed_hostnames | {self.connection.getsockname()[0]}
            if parsed_host.hostname not in allowed:
                return False
            if (parsed_host.port or 80) != self.server.server_address[1]:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed_origin = urllib.parse.urlsplit(origin)
                if parsed_origin.scheme != "http" or parsed_origin.netloc.lower() != host.lower():
                    return False
            return True
        except ValueError:
            return False

    def _authorize_api(self) -> bool:
        supplied = self.headers.get("X-VibeGuard-Token", "")
        if not self._valid_origin_host() or not hmac.compare_digest(supplied.encode("utf-8"), self.server.api_token.encode("utf-8")):
            self._send_error_json("Dashboard session required or origin not allowed", status=403)
            return False
        return True

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status=status)

    def _send_static_file(self, filename: str, content_type: str) -> None:
        filepath = ASSETS_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            self._send_error_json(f"Static file not found: {filename}", status=404)
            return

        try:
            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self._send_error_json(f"Error reading file: {e}", status=500)

    def _read_json_body(self) -> Dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return {}
        try:
            length = int(length_header)
            if length < 0 or length > 10 * 1024 * 1024:
                raise ValueError("Payload size exceeds allowable limit.")
            raw = self.rfile.read(length).decode("utf-8")
            if not raw.strip():
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            return data
        except Exception as e:
            raise ValueError(f"Failed to parse request JSON body: {e}")

    def do_GET(self) -> None:
        # Rate limiting check
        client_ip = self.client_address[0]
        if not self.server.rate_limiter.is_allowed(client_ip):
            self._send_error_json("Rate limit exceeded: max 100 requests per 60 seconds", status=429)
            return

        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        if not self._valid_origin_host():
            self._send_error_json("Origin or Host not allowed", status=403)
            return
        if path.startswith("/api/") and not self._authorize_api():
            return

        if path in ("/", "/index.html"):
            self._send_static_file("index.html", "text/html; charset=utf-8")
            return
        elif path == "/style.css":
            self._send_static_file("style.css", "text/css; charset=utf-8")
            return
        elif path == "/app.js":
            self._send_static_file("app.js", "application/javascript; charset=utf-8")
            return
        elif path == "/api.js":
            self._send_static_file("api.js", "application/javascript; charset=utf-8")
            return
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # REST API endpoints
        if not self.service:
            self._send_error_json("VibeGuard service not initialized", status=500)
            return

        try:
            if path == "/api/project":
                self._send_json(self.service.get_project_info())
            elif path == "/api/prompt":
                self._send_json(self.service.get_prompt())
            elif path == "/api/symbols":
                self._send_json(self.service.get_symbols())
            elif path == "/api/dependencies":
                self._send_json(self.service.get_dependencies())
            elif path == "/api/checkpoints":
                self._send_json(self.service.get_checkpoints())
            elif path in ("/api/badge", "/api/badge.svg"):
                query_params = urllib.parse.parse_qs(parsed_url.query)
                raw = "svg" in query_params.get("format", []) or query_params.get("raw") == ["1"] or path.endswith(".svg")
                badge_data = self.service.get_badge()
                if raw:
                    svg_bytes = badge_data["svg"].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(svg_bytes)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(svg_bytes)
                else:
                    self._send_json(badge_data)
            else:
                self._send_error_json(f"Endpoint not found: {path}", status=404)
        except Exception as e:
            self._send_error_json(str(e), status=500)

    def do_POST(self) -> None:
        # Rate limiting check
        client_ip = self.client_address[0]
        if not self.server.rate_limiter.is_allowed(client_ip):
            self._send_error_json("Rate limit exceeded: max 100 requests per 60 seconds", status=429)
            return

        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        if not self._authorize_api():
            return

        if not self.service:
            self._send_error_json("VibeGuard service not initialized", status=500)
            return

        try:
            body = self._read_json_body()
        except ValueError as ve:
            self._send_error_json(str(ve), status=400)
            return

        try:
            if path == "/api/select-project":
                project_path = body.get("path")
                if not project_path or not str(project_path).strip():
                    self._send_error_json("Field 'path' is required", status=400)
                    return
                result = self.service.select_project(str(project_path).strip())
                self._send_json({"success": True, "project": result})

            elif path == "/api/check":
                result = self.service.run_check(
                    staged=bool(body.get("staged", False)),
                    diff=bool(body.get("diff", False)),
                    patch=bool(body.get("patch", False)),
                    sarif=bool(body.get("sarif", False)),
                )
                self._send_json(result)

            elif path == "/api/baseline":
                result = self.service.update_baseline()
                self._send_json(result)

            elif path == "/api/hook":
                action = body.get("action", "install")
                result = self.service.manage_hook(action=action)
                self._send_json(result)

            elif path == "/api/patch":
                result = self.service.generate_patch()
                self._send_json(result)

            elif path == "/api/config":
                result = self.service.update_config(body)
                self._send_json(result)

            elif path == "/api/badge":
                result = self.service.get_badge()
                self._send_json(result)

            elif path == "/api/checkpoint":
                result = self.service.create_checkpoint(message=body.get("message"))
                self._send_json({"success": True, "checkpoint": result})

            elif path == "/api/rewind":
                result = self.service.rewind(checkpoint_id=body.get("checkpoint_id"), step=int(body.get("step", 1) or 1))
                self._send_json({"success": True, "rewind": result})

            else:
                self._send_error_json(f"Endpoint not found: {path}", status=404)

        except ValueError as ve:
            self._send_error_json(str(ve), status=400)
        except RuntimeError as re:
            self._send_error_json(str(re), status=400)
        except Exception as e:
            self._send_error_json(str(e), status=500)

    def do_PUT(self) -> None:
        self._send_error_json("Method not allowed", status=405)

    def do_DELETE(self) -> None:
        self._send_error_json("Method not allowed", status=405)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server so concurrent requests do not block."""
    daemon_threads = True
    allow_reuse_address = True


def create_server(
    host: str = "127.0.0.1",
    port: int = 8585,
    service: Optional[VibeGuardService] = None,
    allow_fallback_port: bool = False,
) -> ThreadedHTTPServer:
    """Instantiates and configures ThreadedHTTPServer with optional port fallback."""
    curr_port = port
    while True:
        try:
            server = ThreadedHTTPServer((host, curr_port), VibeGuardRequestHandler)
            break
        except OSError as e:
            if allow_fallback_port and (e.errno == 98 or "address already in use" in str(e).lower()) and curr_port < port + 25:
                curr_port += 1
                continue
            raise
    server.service = service or VibeGuardService()
    server.api_token = secrets.token_urlsafe(32)
    server.allowed_hostnames = {host.lower(), "localhost", "127.0.0.1", server.server_address[0]}
    server.rate_limiter = RateLimiter(max_requests=100, window_secs=60)
    return server


def run_server(
    host: str = "127.0.0.1",
    port: int = 8585,
    open_browser: bool = True,
    project_path: Optional[str] = None,
) -> None:
    """Launches the VibeGuard Dashboard web server."""
    service = VibeGuardService(project_path)
    server = create_server(host=host, port=port, service=service, allow_fallback_port=True)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/#token={server.api_token}"

    print(f"\n🛡️  VIBEGUARD DASHBOARD ONLINE")
    print(f"   Project: {service.project_root}")
    print(f"   URL:     {url}")
    print(f"   Press Ctrl+C to stop server.\n")

    if open_browser:
        def _launch_browser() -> None:
            time.sleep(0.4)
            webbrowser.open(url)

        threading.Thread(target=_launch_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 VibeGuard Dashboard stopped.")
    finally:
        server.server_close()
