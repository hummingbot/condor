import pytest
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.hummingbot_client_factory import build_ssl_option


def test_build_ssl_option_verify_disabled_returns_false():
    assert build_ssl_option(tls_verify=False) is False


def test_build_ssl_option_missing_ca_bundle_raises():
    with pytest.raises(ValueError):
        build_ssl_option(tls_verify=True, ca_bundle_path="/tmp/does-not-exist-ca.pem")


def test_build_ssl_option_client_cert_requires_key():
    with pytest.raises(ValueError):
        build_ssl_option(tls_verify=True, client_cert_path="/tmp/cert.pem")


def test_build_ssl_option_client_key_requires_cert():
    with pytest.raises(ValueError):
        build_ssl_option(tls_verify=True, client_key_path="/tmp/key.pem")
