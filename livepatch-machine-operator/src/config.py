# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Typed charm configuration for the Livepatch machine charm.

The Juju configuration options for this charm use dotted names (for example
``server.url-template``), which cannot be mapped onto Python identifiers by
``ops.CharmBase.load_config``. The configuration is therefore read explicitly
by :meth:`CharmConfig.from_charm`.

Only the handful of options the charm actually reasons about are typed. Every
other option is passed straight through to the snap, so the raw mapping is
retained as-is.
"""

from __future__ import annotations

import logging
from base64 import b64decode
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Prefix under which the whole charm configuration is stored in snap config.
SNAP_CONFIG_PREFIX = "lp"

# Juju configuration options the charm reasons about.
OPT_URL_TEMPLATE = "server.url-template"
OPT_SERVER_ADDRESS = "server.server-address"
OPT_CONTRACTS_CA = "contracts.ca"
OPT_PATCH_STORAGE_POSTGRES = "patch-storage.postgres-connection-string"
OPT_PATCH_SYNC_ENABLED = "patch-sync.enabled"

# Snap configuration keys the charm computes rather than passes through.
KEY_IS_LEADER = "server.is-leader"
KEY_DB_CONNECTION_STRING = "database.connection-string"
KEY_PATCH_SYNC_ID = "patch-sync.id"
KEY_CONTRACTS_ENABLED = "contracts.enabled"
KEY_CONTRACTS_URL = "contracts.url"
KEY_CONTRACTS_USER = "contracts.user"
# The value below is a configuration key name, not a credential.
KEY_CONTRACTS_PASSWORD = "contracts.password"  # nosec B105
KEY_AUTH_BASIC_ENABLED = "auth.basic.enabled"
KEY_AUTH_BASIC_USERS = "auth.basic.users"


class InvalidCharmConfigError(Exception):
    """Raised when the charm configuration fails validation."""


def parse_server_port(server_address: Optional[str]) -> Optional[str]:
    """Extract the port from a ``host:port`` server address.

    Args:
        server_address: the raw ``server.server-address`` configuration value.

    Returns:
        The port as a string, or None if the address is missing or malformed.
    """
    if not server_address:
        return None
    parts = server_address.split(":")
    if len(parts) != 2:
        return None
    port = parts[1].strip()
    return port or None


def _decode_contracts_ca(encoded: Optional[str]) -> Tuple[Optional[str], bool]:
    """Decode the base64-encoded ``contracts.ca`` configuration option.

    A malformed value is logged and ignored rather than blocking the charm,
    which preserves the behaviour of earlier charm revisions.

    Args:
        encoded: the raw base64 configuration value, if set.

    Returns:
        The decoded PEM text and whether a configured value was undecodable.
    """
    if not encoded:
        return None, False
    try:
        return b64decode(encoded).decode("utf8"), False
    except Exception:  # noqa: B902 - any decoding failure is treated the same
        logger.error("failed to parse base64 value of `contracts.ca` config option")
        return None, True


@dataclass(frozen=True)
class CharmConfig:
    """Validated view over the charm's Juju configuration.

    Attributes:
        raw: the complete, unmodified Juju configuration mapping.
        url_template: the validated ``server.url-template`` option.
        server_address: the validated ``server.server-address`` option.
        server_port: the port parsed out of ``server_address``.
        contracts_ca: the decoded ``contracts.ca`` PEM, if any.
        has_invalid_contracts_ca: whether ``contracts.ca`` was configured but
            could not be decoded.
        patch_storage_postgres: the ``patch-storage.postgres-connection-string``
            option, which falls back to the database URI when empty.
        patch_sync_enabled: whether patch synchronisation is enabled.
    """

    raw: Dict[str, Any]
    url_template: str
    server_address: str
    server_port: str
    contracts_ca: Optional[str]
    has_invalid_contracts_ca: bool
    patch_storage_postgres: str
    patch_sync_enabled: bool

    @classmethod
    def from_charm(cls, charm: Any) -> "CharmConfig":
        """Build a validated configuration object from a charm instance.

        Args:
            charm: the charm whose ``config`` mapping should be read.

        Returns:
            The validated configuration.

        Raises:
            InvalidCharmConfigError: if a required option is unset or malformed.
        """
        raw: Dict[str, Any] = {**charm.config}

        url_template = str(raw.get(OPT_URL_TEMPLATE) or "")
        if not url_template:
            raise InvalidCharmConfigError("✘ server.url-template config not set")

        server_address = str(raw.get(OPT_SERVER_ADDRESS) or "")
        server_port = parse_server_port(server_address)
        if server_port is None:
            raise InvalidCharmConfigError("✘ server.server-address config must be in `host:port` form")
        contracts_ca, has_invalid_contracts_ca = _decode_contracts_ca(raw.get(OPT_CONTRACTS_CA))

        return cls(
            raw=raw,
            url_template=url_template,
            server_address=server_address,
            server_port=server_port,
            contracts_ca=contracts_ca,
            has_invalid_contracts_ca=has_invalid_contracts_ca,
            patch_storage_postgres=str(raw.get(OPT_PATCH_STORAGE_POSTGRES) or ""),
            patch_sync_enabled=raw.get(OPT_PATCH_SYNC_ENABLED) is True,
        )

    def to_snap_config(
        self,
        *,
        db_uri: str,
        is_leader: bool,
        model_uuid: str,
        pro_airgapped_url: Optional[str],
    ) -> Dict[str, Any]:
        """Render the desired snap configuration.

        Every Juju configuration option is passed through to the snap under the
        ``lp.`` prefix, with the charm-computed values layered on top.

        Args:
            db_uri: the resolved PostgreSQL connection string.
            is_leader: whether this unit is the Juju leader.
            model_uuid: the Juju model UUID, used as the patch-sync identifier.
            pro_airgapped_url: the air-gapped Pro contracts URL, if related.

        Returns:
            A flat mapping of prefixed snap configuration keys to values.
        """
        configuration: Dict[str, Any] = {**self.raw}

        configuration[KEY_IS_LEADER] = is_leader
        configuration[KEY_DB_CONNECTION_STRING] = db_uri

        # The dedicated patch storage connection string falls back to the
        # database relation's URI when the operator has not set one.
        if not self.patch_storage_postgres:
            configuration[OPT_PATCH_STORAGE_POSTGRES] = db_uri

        if self.patch_sync_enabled:
            configuration[KEY_PATCH_SYNC_ID] = model_uuid

        if pro_airgapped_url:
            configuration[KEY_CONTRACTS_ENABLED] = True
            configuration[KEY_CONTRACTS_URL] = pro_airgapped_url
            configuration.pop(KEY_CONTRACTS_USER, None)
            configuration.pop(KEY_CONTRACTS_PASSWORD, None)

        return {f"{SNAP_CONFIG_PREFIX}.{key}": value for key, value in configuration.items()}
