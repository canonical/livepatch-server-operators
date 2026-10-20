# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from base64 import b64encode

import pytest
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


def test_decode_contracts_ca_accepts_line_wrapped_base64():
    cert = "-----BEGIN CERTIFICATE-----\n" + "A" * 120 + "\n-----END CERTIFICATE-----\n"
    encoded = b64encode(cert.encode()).decode()
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    assert decode_contracts_ca(wrapped) == (cert, False)


@pytest.mark.parametrize("value", ["!!!!", "not-base64!!!", "QUJD!", "QUJ"])
def test_decode_contracts_ca_rejects_non_alphabet_or_bad_padding(value):
    assert decode_contracts_ca(value) == (None, True)


def test_decode_contracts_ca_malformed():
    pem, invalid = decode_contracts_ca("not-base64!!!")
    assert pem is None
    assert invalid is True
