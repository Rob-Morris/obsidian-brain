"""Tests for obsidian_cli — IPC socket client for the native Obsidian CLI."""

import json
import socket
from unittest.mock import patch, MagicMock

import pytest

import obsidian_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_send(response):
    """Patch _send to return a fixed response."""
    return patch.object(obsidian_cli, "_send", return_value=response)


def _mock_socket_exists(exists):
    """Patch _socket_exists to return a fixed value."""
    return patch.object(obsidian_cli, "_socket_exists", return_value=exists)


# ---------------------------------------------------------------------------
# _get_socket_path
# ---------------------------------------------------------------------------

class TestGetSocketPath:
    def test_unix_path(self):
        with patch.object(obsidian_cli.platform, "system", return_value="Darwin"):
            path = obsidian_cli._get_socket_path()
            assert path.endswith(".obsidian-cli.sock")

    def test_linux_path(self):
        with patch.object(obsidian_cli.platform, "system", return_value="Linux"):
            path = obsidian_cli._get_socket_path()
            assert path.endswith(".obsidian-cli.sock")

    def test_windows_path(self):
        with patch.object(obsidian_cli.platform, "system", return_value="Windows"), \
             patch.object(obsidian_cli.getpass, "getuser", return_value="testuser"):
            path = obsidian_cli._get_socket_path()
            assert "obsidian-cli-testuser" in path


# ---------------------------------------------------------------------------
# _socket_exists
# ---------------------------------------------------------------------------

class TestSocketExists:
    def test_returns_true_when_socket_file_present(self):
        with patch.object(obsidian_cli.platform, "system", return_value="Darwin"), \
             patch.object(obsidian_cli.os.path, "exists", return_value=True):
            assert obsidian_cli._socket_exists() is True

    def test_returns_false_when_socket_file_missing(self):
        with patch.object(obsidian_cli.platform, "system", return_value="Darwin"), \
             patch.object(obsidian_cli.os.path, "exists", return_value=False):
            assert obsidian_cli._socket_exists() is False

    def test_windows_always_returns_true(self):
        """Named pipes aren't stat-able; must try connect."""
        with patch.object(obsidian_cli.platform, "system", return_value="Windows"):
            assert obsidian_cli._socket_exists() is True


# ---------------------------------------------------------------------------
# check_available
# ---------------------------------------------------------------------------

class TestCheckAvailable:
    def test_returns_false_when_no_socket(self):
        with _mock_socket_exists(False):
            assert obsidian_cli.check_available() is False

    def test_returns_true_with_working_socket(self):
        with _mock_socket_exists(True), _mock_send("1.12.7 (installer 1.8.9)"):
            assert obsidian_cli.check_available() is True

    def test_returns_false_on_connection_failure(self):
        with _mock_socket_exists(True), _mock_send(None):
            assert obsidian_cli.check_available() is False

    def test_returns_false_on_empty_response(self):
        with _mock_socket_exists(True), _mock_send(""):
            assert obsidian_cli.check_available() is False


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_none_when_send_fails(self):
        with _mock_send(None):
            assert obsidian_cli.search("vault", "test") is None

    def test_parses_json_path_list(self):
        paths = ["Wiki/test.md", "Wiki/other.md"]
        with _mock_send(json.dumps(paths)):
            result = obsidian_cli.search("vault", "test")
            assert result == paths

    def test_returns_none_on_non_list_response(self):
        with _mock_send('{"error": "bad"}'):
            assert obsidian_cli.search("vault", "test") is None

    def test_returns_none_on_invalid_json(self):
        with _mock_send("not json"):
            assert obsidian_cli.search("vault", "test") is None

    def test_includes_vault_param(self):
        with patch.object(obsidian_cli, "_send", return_value="[]") as mock:
            obsidian_cli.search("Brain", "query")
            argv = mock.call_args[0][0]
            assert "vault=Brain" in argv

    def test_omits_vault_when_empty(self):
        with patch.object(obsidian_cli, "_send", return_value="[]") as mock:
            obsidian_cli.search("", "query")
            argv = mock.call_args[0][0]
            assert not any(a.startswith("vault=") for a in argv)


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

class TestMove:
    def test_returns_none_on_failure(self):
        with _mock_send(None):
            assert obsidian_cli.move("vault", "a.md", "b.md") is None

    def test_returns_true_on_success(self):
        with _mock_send("Moved a.md to b.md"):
            assert obsidian_cli.move("vault", "a.md", "b.md") is True

    def test_includes_vault_param(self):
        with patch.object(obsidian_cli, "_send", return_value="ok") as mock:
            obsidian_cli.move("Brain", "a.md", "b.md")
            argv = mock.call_args[0][0]
            assert "vault=Brain" in argv


# ---------------------------------------------------------------------------
# _send (integration-style with mock socket)
# ---------------------------------------------------------------------------

class TestSend:
    def test_sends_correct_payload(self):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"ok", b""]
        with patch("obsidian_cli.socket.socket", return_value=mock_sock):
            result = obsidian_cli._send(["version"])
            sent = mock_sock.sendall.call_args[0][0].decode("utf-8")
            payload = json.loads(sent.strip())
            assert payload == {"argv": ["version"], "tty": False, "cwd": "/tmp"}
            assert result == "ok"

    def test_returns_none_on_connection_refused(self):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError
        with patch("obsidian_cli.socket.socket", return_value=mock_sock):
            assert obsidian_cli._send(["version"]) is None

    def test_returns_none_on_timeout(self):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = socket.timeout
        with patch("obsidian_cli.socket.socket", return_value=mock_sock):
            assert obsidian_cli._send(["version"]) is None

    def test_concatenates_chunks(self):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"hel", b"lo", b""]
        with patch("obsidian_cli.socket.socket", return_value=mock_sock):
            assert obsidian_cli._send(["version"]) == "hello"
