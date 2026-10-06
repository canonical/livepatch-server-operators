# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Canonical charm-configuration secret mappings shared by both charms.

Both the machine and k8s charms expose an identical set of Juju configuration
options, including the same ``type: secret`` options that let operators supply
sensitive values through Juju secrets instead of plain-text config. The mapping
between those secret options and the plain options they override is domain
knowledge that must stay identical across the two charms, so it lives here as a
single source of truth.

Option names use the canonical Juju form (dotted, with dashes preserved, for
example ``patch-storage.swift-api-key``). Each charm adapts these canonical
names to its own output format: the machine charm renders snap configuration
keys (``lp.<option>``) while the k8s charm renders ``LP_*`` environment
variables.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

# The key under which a standalone ``type: secret`` option's value is expected
# to be stored in the Juju secret's content.
SECRET_VALUE_KEY = "value"  # nosec B105

# Standalone plain options with a preferred, Juju-secret-backed sibling. Maps
# the plain option name to its ``type: secret`` sibling option name. When the
# sibling secret is set it overrides the deprecated plain-text option; otherwise
# the plain option's value is kept as-is.
SECRET_FALLBACK_OPTIONS: Dict[str, str] = {
    "auth.basic.users": "auth.basic.users-secret",
    "patch-storage.swift-api-key": "patch-storage.swift-api-key-secret",
    "patch-storage.gcs-credentials-json": "patch-storage.gcs-credentials-json-secret",
    "patch-storage.ibm-api-key": "patch-storage.ibm-api-key-secret",
    "patch-sync.token": "patch-sync.token-secret",
    "influx.token": "influx.token-secret",
}

# Consolidated credential-group secret options that bundle several related,
# deprecated plain-text options into a single Juju secret (so operators manage
# one secret per group). Maps each group's ``type: secret`` option name to a
# mapping of the expected secret content key to the plain option it expands
# into. Content keys the secret does not provide leave the corresponding plain
# option untouched.
SECRET_GROUPS: Dict[str, Dict[str, str]] = {
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


class MissingSecretValueError(Exception):
    """Raised when a standalone secret option is set but lacks its value key."""

    def __init__(self, option: str) -> None:
        """Initialise the error.

        Args:
            option: the secret option whose content is missing the value key.
        """
        self.option = option
        super().__init__(f"the secret configured for `{option}` must have a `{SECRET_VALUE_KEY}` key")


def resolve_secret_values(
    get_content: Callable[[str], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Resolve the plain options overridden by every secret option that is set.

    Iterates the standalone (:data:`SECRET_FALLBACK_OPTIONS`) and credential
    group (:data:`SECRET_GROUPS`) secret options, reading each configured
    secret's content and mapping it onto the plain options it overrides. Only
    values sourced from a set secret are returned; plain-text options without a
    corresponding secret are left for the caller to pass through unchanged.

    Args:
        get_content: a callback that, given a secret option name, returns the
            content mapping of the Juju secret configured for that option, or
            ``None`` when the option is unset.

    Returns:
        A mapping of canonical plain option name to the value resolved from its
        secret. Options whose secret is unset are absent from the mapping.

    Raises:
        MissingSecretValueError: if a standalone secret is set but its content
            does not include the expected value key.
    """
    resolved: Dict[str, Any] = {}

    for plain_option, secret_option in SECRET_FALLBACK_OPTIONS.items():
        content = get_content(secret_option)
        if content is None:
            continue
        value = content.get(SECRET_VALUE_KEY)
        if value is None:
            raise MissingSecretValueError(secret_option)
        resolved[plain_option] = value

    for group_option, sub_keys in SECRET_GROUPS.items():
        content = get_content(group_option)
        if content is None:
            continue
        for content_key, target_option in sub_keys.items():
            value = content.get(content_key)
            if value is not None:
                resolved[target_option] = value

    return resolved
