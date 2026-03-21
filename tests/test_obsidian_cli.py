"""Tests for obsidian_cli — HTTP client for the Obsidian CLI REST endpoint."""

import json
import os
from contextlib import contextmanager
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from unittest.mock import patch

import pytest

import obsidian_cli


@contextmanager
def _mock_server(handler_class):
    """Start a throwaway HTTP server in a background thread, yield its port."""
    ready = Event()

    class _ReadyServer(HTTPServer):
        def service_actions(self):
            ready.set()

    server = _ReadyServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready.wait(timeout=2.0)
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


class _SilentHandler(BaseHTTPRequestHandler):
    """Suppress request logging."""
    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# check_available
# ---------------------------------------------------------------------------

class TestCheckAvailable:
    def test_returns_false_when_no_server(self):
        """Should return False when nothing is listening."""
        with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", "http://localhost:19999"):
            assert obsidian_cli.check_available() is False

    def test_returns_true_with_mock_server(self):
        """Should return True when a server responds 200."""
        class Handler(_SilentHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        with _mock_server(Handler) as port:
            with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", f"http://127.0.0.1:{port}"):
                assert obsidian_cli.check_available() is True

    def test_returns_false_on_timeout(self):
        """Should return False on connection timeout (unreachable host)."""
        # Use a non-routable IP to force timeout
        with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", "http://192.0.2.1:27124"):
            assert obsidian_cli.check_available() is False


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_none_when_no_server(self):
        with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", "http://localhost:19999"):
            assert obsidian_cli.search("vault", "test") is None

    def test_parses_result_list(self):
        """Should parse a JSON array response."""
        results = [
            {"filename": "Wiki/test.md", "score": 1.5, "matches": [{"content": "test content"}]},
            {"filename": "Wiki/other.md", "score": 0.8, "matches": []},
        ]

        class Handler(_SilentHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(results).encode())

        with _mock_server(Handler) as port:
            with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", f"http://127.0.0.1:{port}"):
                got = obsidian_cli.search("vault", "test")
                assert got is not None
                assert len(got) == 2
                assert got[0]["filename"] == "Wiki/test.md"

    def test_returns_none_on_non_list_response(self):
        """Should return None if response is not a list."""
        class Handler(_SilentHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "bad"}')

        with _mock_server(Handler) as port:
            with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", f"http://127.0.0.1:{port}"):
                assert obsidian_cli.search("vault", "test") is None


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

class TestMove:
    def test_returns_none_when_no_server(self):
        with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", "http://localhost:19999"):
            assert obsidian_cli.move("vault", "a.md", "b.md") is None

    def test_parses_result(self):
        """Should parse a JSON object response."""
        result = {"status": "ok", "links_updated": 3}

        class Handler(_SilentHandler):
            def do_POST(self):
                # Read request body to avoid broken pipe when client is still sending
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        with _mock_server(Handler) as port:
            with patch.object(obsidian_cli, "OBSIDIAN_CLI_URL", f"http://127.0.0.1:{port}"):
                got = obsidian_cli.move("vault", "a.md", "b.md")
                assert got is not None
                assert got["links_updated"] == 3
