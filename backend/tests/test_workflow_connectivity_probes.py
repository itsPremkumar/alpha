"""Ready-made connectivity probe implementations.

These pin the three probe factories (HTTP, TCP, DNS) that a host installs
via ``engine.connectivity_probe = ...``. Each factory returns a callable that
reports reachable/unreachable without ever raising.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alpha.workflow.connectivity import dns_probe, http_probe, tcp_probe

# ------------------------------------------------------------------ http_probe


def test_http_probe_returns_true_on_expected_status():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        probe = http_probe("https://example.com/health")
        assert probe() is True


def test_http_probe_returns_false_on_wrong_status():
    mock_response = MagicMock()
    mock_response.status = 503
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        probe = http_probe("https://example.com/health")
        assert probe() is False


def test_http_probe_returns_false_on_connection_error():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        probe = http_probe("https://example.com/health")
        assert probe() is False


def test_http_probe_returns_false_on_timeout():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        probe = http_probe("https://example.com/health", timeout_seconds=0.1)
        assert probe() is False


def test_http_probe_custom_expected_status():
    mock_response = MagicMock()
    mock_response.status = 204
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        probe = http_probe("https://example.com/health", expected_status=204)
        assert probe() is True


# ------------------------------------------------------------------- tcp_probe


def test_tcp_probe_returns_true_on_successful_connection():
    with patch("socket.create_connection") as mock_conn:
        mock_conn.return_value.__enter__ = MagicMock(return_value=None)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        probe = tcp_probe("example.com", 443)
        assert probe() is True


def test_tcp_probe_returns_false_on_refused():
    with patch("socket.create_connection", side_effect=OSError("refused")):
        probe = tcp_probe("example.com", 443)
        assert probe() is False


def test_tcp_probe_returns_false_on_timeout():
    with patch("socket.create_connection", side_effect=TimeoutError("timed out")):
        probe = tcp_probe("example.com", 443, timeout_seconds=0.1)
        assert probe() is False


# ------------------------------------------------------------------- dns_probe


def test_dns_probe_returns_true_on_successful_resolution():
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        probe = dns_probe("example.com")
        assert probe() is True


def test_dns_probe_returns_false_on_resolution_failure():
    with patch("socket.getaddrinfo", side_effect=OSError("name or service not known")):
        probe = dns_probe("nonexistent.invalid")
        assert probe() is False


def test_dns_probe_returns_false_on_timeout():
    with patch("socket.getaddrinfo", side_effect=TimeoutError("timed out")):
        probe = dns_probe("example.com", timeout_seconds=0.1)
        assert probe() is False
