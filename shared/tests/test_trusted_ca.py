# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from base64 import b64encode

from livepatch_common.trusted_ca import TRUSTED_CA_FILENAME, decode_contracts_ca


def test_trusted_ca_filename_is_shared_path():
    assert TRUSTED_CA_FILENAME == "/usr/local/share/ca-certificates/trusted-contracts.ca.crt"


def test_decode_contracts_ca_none():
    assert decode_contracts_ca(None) == (None, False)
    assert decode_contracts_ca("") == (None, False)


def test_decode_contracts_ca_valid():
    cert = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n"
    pem, invalid = decode_contracts_ca(b64encode(cert.encode()).decode())
    assert pem == cert
    assert invalid is False


def test_decode_contracts_ca_malformed():
    pem, invalid = decode_contracts_ca("not-base64!!!")
    assert pem is None
    assert invalid is True