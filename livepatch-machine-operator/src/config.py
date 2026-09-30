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
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import ops
from livepatch_common.config import (
    SECRET_FALLBACK_OPTIONS,
    SECRET_GROUPS,
    SECRET_VALUE_KEY,
    MissingSecretValueError,
    resolve_secret_values,
)
from livepatch_common.trusted_ca import decode_contracts_ca

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
KEY_TIMESCALE_DB_CONNECTION_STRING = "timescale_db.connection-string"
KEY_CVE_SYNC_SOURCE_URL = "cve-sync.source-url"
KEY_LSN_SYNC_SOURCE_URL = "lsn-sync.source-url"
KEY_OTEL_METRICS_OTLP_ENDPOINT = "otel-metrics.otlp-endpoint"
KEY_OTEL_METRICS_PROTOCOL = "otel-metrics.protocol"
KEY_OTEL_METRICS_INSECURE = "otel-metrics.insecure"
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


def _get_secret_content(charm: Any, key: str, secret_id: str) -> Dict[str, Any]:
    """Fetch the content of the Juju secret configured for a given option.

    Args:
        charm: the charm whose secret should be fetched.
        key: the config option name the secret is configured for, for errors.
        secret_id: the secret URI configured for the option.

    Returns:
        The secret's content.

    Raises:
        InvalidCharmConfigError: if the secret is inaccessible.
    """
    try:
        secret = charm.model.get_secret(id=secret_id)
        return secret.get_content(refresh=True)
    except ops.SecretNotFoundError as exc:
        raise InvalidCharmConfigError(
            f"✘ could not access the secret configured for `{key}`; run "
            f"`juju grant-secret <secret> {charm.app.name}` and try again."
        ) from exc


def _resolve_secret_options(charm: Any, raw: Dict[str, Any]) -> None:
    """Resolve preferred `type: secret` config options in-place, over their
    deprecated plain-text counterparts.

    Standalone secrets (`SECRET_FALLBACK_OPTIONS`) overwrite the corresponding
    plain option's raw value when set; otherwise the plain option's existing
    raw value (already in `raw`) is left untouched. Credential-group secrets
    (`SECRET_GROUPS`) are popped and, when set, expanded into the individual
    raw config keys they represent; unset sub-keys leave the plain option's
    existing raw value untouched.

    Args:
        charm: the charm whose secrets should be resolved.
        raw: the raw Juju configuration mapping, mutated in-place.

    Raises:
        InvalidCharmConfigError: if a configured secret is inaccessible, or a
            standalone secret is set but missing its expected content key.
    """

    def get_content(option: str) -> Optional[Dict[str, Any]]:
        secret_id = raw.get(option)
        if not secret_id:
            return None
        return _get_secret_content(charm, option, secret_id)

    try:
        resolved = resolve_secret_values(get_content)
    except MissingSecretValueError as exc:
        raise InvalidCharmConfigError(
            f"✘ the secret configured for `{exc.option}` must have a `{SECRET_VALUE_KEY}` key"
        ) from exc

    for secret_key in SECRET_FALLBACK_OPTIONS.values():
        raw.pop(secret_key, None)
    for group_key in SECRET_GROUPS:
        raw.pop(group_key, None)
    raw.update(resolved)


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
        _resolve_secret_options(charm, raw)

        url_template = str(raw.get(OPT_URL_TEMPLATE) or "")
        if not url_template:
            raise InvalidCharmConfigError("✘ server.url-template config not set")

        server_address = str(raw.get(OPT_SERVER_ADDRESS) or "")
        server_port = parse_server_port(server_address)
        if server_port is None:
            raise InvalidCharmConfigError("✘ server.server-address config must be in `host:port` form")
        contracts_ca, has_invalid_contracts_ca = decode_contracts_ca(raw.get(OPT_CONTRACTS_CA))

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
        timescale_db_uri: Optional[str] = None,
        cve_catalog_url: Optional[str] = None,
        otel_metrics_endpoint: Optional[Tuple[str, str, bool]] = None,
    ) -> Dict[str, Any]:
        """Render the desired snap configuration.

        Every Juju configuration option is passed through to the snap under the
        ``lp.`` prefix, with the charm-computed values layered on top.

        Args:
            db_uri: the resolved PostgreSQL connection string.
            is_leader: whether this unit is the Juju leader.
            model_uuid: the Juju model UUID, used as the patch-sync identifier.
            pro_airgapped_url: the air-gapped Pro contracts URL, if related.
            timescale_db_uri: the timescale/metrics database connection string,
                if the metrics-db relation is established.
            cve_catalog_url: the CVE catalog service URL, if related.
            otel_metrics_endpoint: the (endpoint, protocol, insecure) OTLP
                metrics push target from the send-otlp relation, if related.

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

        # The connection string for the timescale metrics database is always
        # rendered, so removing the relation clears the snap key (snapd drops
        # options set to an empty string).
        configuration[KEY_TIMESCALE_DB_CONNECTION_STRING] = timescale_db_uri or ""

        # The related CVE catalog serves both CVE and LSN data, so it overrides
        # the manual cve-sync.source-url/lsn-sync.source-url options on every
        # unit. When unrelated, the options' empty defaults apply unchanged.
        if cve_catalog_url:
            configuration[KEY_CVE_SYNC_SOURCE_URL] = cve_catalog_url
            configuration[KEY_LSN_SYNC_SOURCE_URL] = cve_catalog_url

        # The send-otlp relation provides the OTLP metrics push endpoint.
        # Always rendered so a removed relation clears the snap keys.
        if otel_metrics_endpoint:
            endpoint, protocol, insecure = otel_metrics_endpoint
            configuration[KEY_OTEL_METRICS_OTLP_ENDPOINT] = endpoint
            configuration[KEY_OTEL_METRICS_PROTOCOL] = protocol
            configuration[KEY_OTEL_METRICS_INSECURE] = insecure
        else:
            configuration[KEY_OTEL_METRICS_OTLP_ENDPOINT] = ""
            configuration[KEY_OTEL_METRICS_PROTOCOL] = ""
            configuration[KEY_OTEL_METRICS_INSECURE] = ""

        return {f"{SNAP_CONFIG_PREFIX}.{key}": value for key, value in configuration.items()}
