import pytest
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from utils.url_builder import ServerUrlError, build_server_url


def test_http_default_non_443_port():
    assert build_server_url("localhost", 8000) == "http://localhost:8000"


def test_https_default_on_port_443():
    assert build_server_url("api.example.com", 443) == "https://api.example.com"


def test_explicit_https_host_keeps_scheme():
    assert build_server_url("https://api.example.com", 443) == "https://api.example.com"


def test_explicit_https_host_custom_port():
    assert (
        build_server_url("https://api.example.com", 8443)
        == "https://api.example.com:8443"
    )


def test_explicit_http_host_default_port_omitted():
    assert build_server_url("http://api.example.com", 80) == "http://api.example.com"


def test_protocol_override_forces_https():
    assert (
        build_server_url("api.example.com", 8000, protocol="https")
        == "https://api.example.com:8000"
    )


def test_conflicting_port_in_host_and_argument_raises():
    with pytest.raises(ServerUrlError):
        build_server_url("https://api.example.com:9000", 8000)


def test_conflicting_protocol_raises():
    with pytest.raises(ServerUrlError):
        build_server_url("https://api.example.com", 443, protocol="http")
