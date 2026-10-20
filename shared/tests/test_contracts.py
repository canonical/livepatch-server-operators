# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

from livepatch_common.contracts import (
    DEFAULT_CONTRACTS_URL,
    RESOURCE_NAME,
    get_machine_token,
    get_resource_token,
    make_request,
)


@patch("livepatch_common.contracts.get_system_information")
def test_get_machine_token_success(mock_sysinfo):
    mock_sysinfo.return_value = {
        "architecture": "amd64",
        "version": "ubuntu",
        "version_id": "24.04",
        "version_codename": "noble",
        "kernel-version": "6.8.0",
    }

    def fake_request(method, url, *args, **kwargs):
        assert method == "POST"
        assert url == f"{DEFAULT_CONTRACTS_URL}/v1/context/machines/token"
        return {"machineToken": "the-machine-token"}

    with patch("livepatch_common.contracts.make_request", side_effect=fake_request):
        assert get_machine_token("contract-token") == "the-machine-token"


def test_get_machine_token_failure_returns_none():
    def fake_request(*args, **kwargs):
        raise RuntimeError("boom")

    with patch("livepatch_common.contracts.make_request", side_effect=fake_request):
        assert get_machine_token("contract-token") is None


def test_get_resource_token_success():
    def fake_request(method, url, *args, **kwargs):
        assert method == "GET"
        assert url == f"{DEFAULT_CONTRACTS_URL}/v1/resources/{RESOURCE_NAME}/context/machines/livepatch-onprem"
        return {"resourceToken": "the-resource-token"}

    with patch("livepatch_common.contracts.make_request", side_effect=fake_request):
        assert get_resource_token("machine-token") == "the-resource-token"


@patch("livepatch_common.contracts.requests.request")
def test_make_request_delegates_to_requests(mock_request):
    mock_request.return_value.json.return_value = {"ok": True}
    assert make_request("GET", "http://example.com") == {"ok": True}
    mock_request.assert_called_once_with("GET", "http://example.com")


def test_get_machine_token_writes_ca_certificate():
    def fake_request(method, url, *args, **kwargs):
        assert kwargs.get("verify") is not None
        return {"machineToken": "t"}

    with patch("livepatch_common.contracts.make_request", side_effect=fake_request):
        token = get_machine_token("contract-token", ca_certificate=b"-----BEGIN CERTIFICATE-----")
    assert token == "t"
