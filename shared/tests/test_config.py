# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Dict, Optional

import pytest

from livepatch_common.config import (
    SECRET_FALLBACK_OPTIONS,
    SECRET_GROUPS,
    SECRET_VALUE_KEY,
    MissingSecretValueError,
    resolve_secret_values,
)


def _provider(contents: Dict[str, Optional[dict]]):
    """Return a get_content callback backed by a fixed option -> content map."""

    def get_content(option: str) -> Optional[dict]:
        return contents.get(option)

    return get_content


def test_secret_value_key():
    assert SECRET_VALUE_KEY == "value"


def test_canonical_fallback_options_are_the_expected_set():
    assert SECRET_FALLBACK_OPTIONS == {
        "auth.basic.users": "auth.basic.users-secret",
        "patch-storage.swift-api-key": "patch-storage.swift-api-key-secret",
        "patch-storage.swift-temp-url-key": "patch-storage.swift-temp-url-key-secret",
        "patch-storage.gcs-credentials-json": "patch-storage.gcs-credentials-json-secret",
        "patch-storage.ibm-api-key": "patch-storage.ibm-api-key-secret",
        "patch-sync.token": "patch-sync.token-secret",
        "influx.token": "influx.token-secret",
    }


def test_canonical_secret_groups_are_the_expected_set():
    assert SECRET_GROUPS == {
        "contracts.credentials-secret": {
            "user": "contracts.user",
            "password": "contracts.password",
            "ca": "contracts.ca",
        },
        "patch-storage.s3-credentials-secret": {
            "access-key": "patch-storage.s3-access-key",
            "secret-key": "patch-storage.s3-secret-key",
        },
        "patch-storage.azure-credentials-secret": {
            "account-key": "patch-storage.azure-account-key",
            "connection-string": "patch-storage.azure-connection-string",
            "client-secret": "patch-storage.azure-client-secret",
        },
    }


def test_resolve_returns_empty_when_no_secrets_set():
    assert resolve_secret_values(_provider({})) == {}


def test_resolve_standalone_secret_overrides_plain_option():
    resolved = resolve_secret_values(
        _provider({"patch-sync.token-secret": {SECRET_VALUE_KEY: "s3cr3t"}})
    )
    assert resolved == {"patch-sync.token": "s3cr3t"}


def test_resolve_swift_temp_url_key_secret_overrides_plain_option():
    resolved = resolve_secret_values(
        _provider(
            {"patch-storage.swift-temp-url-key-secret": {SECRET_VALUE_KEY: "temp-url-key"}}
        )
    )
    assert resolved == {"patch-storage.swift-temp-url-key": "temp-url-key"}


def test_resolve_standalone_secret_missing_value_key_raises():
    with pytest.raises(MissingSecretValueError) as exc_info:
        resolve_secret_values(_provider({"influx.token-secret": {"wrong": "x"}}))
    assert exc_info.value.option == "influx.token-secret"
    assert "influx.token-secret" in str(exc_info.value)
    assert SECRET_VALUE_KEY in str(exc_info.value)


def test_resolve_group_secret_expands_all_sub_keys():
    resolved = resolve_secret_values(
        _provider(
            {
                "contracts.credentials-secret": {
                    "user": "alice",
                    "password": "hunter2",
                    "ca": "PEM",
                }
            }
        )
    )
    assert resolved == {
        "contracts.user": "alice",
        "contracts.password": "hunter2",
        "contracts.ca": "PEM",
    }


def test_resolve_group_secret_only_expands_provided_sub_keys():
    resolved = resolve_secret_values(
        _provider({"patch-storage.s3-credentials-secret": {"access-key": "AK"}})
    )
    assert resolved == {"patch-storage.s3-access-key": "AK"}


def test_resolve_combines_standalone_and_group_secrets():
    resolved = resolve_secret_values(
        _provider(
            {
                "auth.basic.users-secret": {SECRET_VALUE_KEY: "user:hash"},
                "patch-storage.azure-credentials-secret": {
                    "account-key": "AK",
                    "connection-string": "CS",
                },
            }
        )
    )
    assert resolved == {
        "auth.basic.users": "user:hash",
        "patch-storage.azure-account-key": "AK",
        "patch-storage.azure-connection-string": "CS",
    }
