#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Livepatch k8s charm, built on the 12-factor `go-framework` charm library.

This charm subclasses `paas_charm.go.Charm`/`paas_charm.app.App` to reuse the
reconciler pattern, Pebble service management, database migrations
(`migrate.sh`), ingress, tracing, logging and metrics support the library
provides, while translating charm configuration and relation data into the
`LP_*` environment variables the livepatch-server Go binary expects (see
`config/config.go` in the livepatch-server repo).

Anything not covered by the go-framework extension (a second, metrics/
timescale database, the CVE catalog and pro-airgapped-server relations, OTLP
metrics push, and the legacy contract-token exchange action) is implemented
here directly.
"""

import logging
import typing
from base64 import b64decode
from urllib.parse import urlparse

import ops
import ops.pebble
import paas_charm.app
import paas_charm.charm
import paas_charm.databases
import paas_charm.go
import pgsql
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequiresEvent
from paas_charm.exceptions import CharmConfigInvalidError
from paas_charm.utils import config_get_with_secret
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from paas_charm.databases import PaaSDatabaseRequires

# paas_charm's make_database_requirers iterates through all relations in
# metadata matching SUPPORTED_DB_INTERFACES and attempts to instantiate a
# PaaSDatabaseRequires for each. When a charm defines an additional relation
# with the same interface (like `metrics-db: interface: postgresql_client`),
# make_database_requirers maps it to the same key 'postgresql' and creates
# a duplicate PaaSDatabaseRequires object, raising a RuntimeError.
# We patch make_database_requirers to only create requirers for standard relation names.
_orig_make_database_requirers = paas_charm.databases.make_database_requirers


def _patched_make_database_requirers(
    charm: ops.CharmBase, database_name: str
) -> typing.Dict[str, PaaSDatabaseRequires]:
    databases: dict[str, PaaSDatabaseRequires] = {}
    for req_name, require in charm.framework.meta.requires.items():
        if require.interface_name in paas_charm.databases.SUPPORTED_DB_INTERFACES:
            expected_name = paas_charm.databases.SUPPORTED_DB_INTERFACES[require.interface_name]
            if req_name == expected_name and req_name not in databases:
                databases[req_name] = PaaSDatabaseRequires(
                    charm,
                    relation_name=req_name,
                    database_name=database_name,
                )
    return databases


paas_charm.databases.make_database_requirers = _patched_make_database_requirers
paas_charm.charm.make_database_requirers = _patched_make_database_requirers

import utils
from legacy_constants import map_old_config_to_new_config
from log_redactor import setup_log_redaction

logger = logging.getLogger(__name__)

METRICS_DB_NAME = "livepatch-metrics-db"
METRICS_DB_RELATION = "metrics-db"
CVE_CATALOG_RELATION = "cve-catalog"
PRO_AIRGAPPED_SERVER_RELATION = "pro-airgapped-server"
SEND_OTLP_RELATION = "send-otlp"
DATABASE_LEGACY_RELATION = "database-legacy"
DATABASE_LEGACY_NAME = "livepatch-server"
SCHEMA_TOOL_PATH = "/usr/local/bin/livepatch-schema-tool"
# Template for storing the trusted CA certificate for the contracts service.
TRUSTED_CA_FILENAME = "/usr/local/share/ca-certificates/trusted-contracts.ca.crt"

# Juju secret label used to persist the ua-contracts resource token exchanged
# via the get-resource-token action. A Juju app-owned secret is used instead
# of a custom peer relation because the go-framework extension reserves the
# `peers` key in charmcraft.yaml for its own internal secret-storage peer.
RESOURCE_TOKEN_SECRET_LABEL = "patch-sync-resource-token"
# Juju secret label used to persist the legacy (pgsql interface) database
# connection string. Same reasoning as RESOURCE_TOKEN_SECRET_LABEL above.
LEGACY_DB_SECRET_LABEL = "legacy-database-connection-string"
# The key under which a `type: secret` config option or the `contract-token`
# action param's value is expected to be stored in the secret's content, for
# options that hold a single, standalone value.
SECRET_VALUE_KEY = "value"  # nosec B105

# Standalone config options with a preferred, Juju-secret-backed sibling: the
# secret (if set) takes priority over the deprecated plain-text option. Keyed
# by the plain option's `user_defined_config` key form (dashes underscored,
# dots preserved), mapping to its secret sibling's key form and the `LP_*`
# env var both ultimately feed.
SECRET_FALLBACK_ENV_VARS: dict[str, tuple[str, str]] = {
    "auth.basic.users": ("auth.basic.users_secret", "LP_AUTH_BASIC_USERS"),
    "patch_storage.swift_api_key": ("patch_storage.swift_api_key_secret", "LP_PATCH_STORAGE_SWIFT_API_KEY"),
    "patch_storage.gcs_credentials_json": (
        "patch_storage.gcs_credentials_json_secret",
        "LP_PATCH_STORAGE_GCS_CREDENTIALS_JSON",
    ),
    "patch_storage.ibm_api_key": ("patch_storage.ibm_api_key_secret", "LP_PATCH_STORAGE_IBM_API_KEY"),
    "patch_sync.token": ("patch_sync.token_secret", "LP_PATCH_SYNC_TOKEN"),
    "influx.token": ("influx.token_secret", "LP_INFLUX_TOKEN"),
}

# Config options that bundle several related, deprecated plain-text options
# into a single preferred Juju secret (so operators only have to manage one
# secret per group). Keyed by the secret's `user_defined_config` key form,
# mapping each expected secret content key to the plain option's key form and
# the `LP_*` env var both ultimately feed.
SECRET_GROUP_ENV_VARS: dict[str, dict[str, tuple[str, str]]] = {
    "contracts.credentials_secret": {
        "user": ("contracts.user", "LP_CONTRACTS_USER"),
        "password": ("contracts.password", "LP_CONTRACTS_PASSWORD"),
        "ca": ("contracts.ca", "LP_CONTRACTS_CA"),
    },
    "patch_storage.s3_credentials_secret": {
        "access-key": ("patch_storage.s3_access_key", "LP_PATCH_STORAGE_S3_ACCESS_KEY"),
        "secret-key": ("patch_storage.s3_secret_key", "LP_PATCH_STORAGE_S3_SECRET_KEY"),
    },
    "patch_storage.azure_credentials_secret": {
        "account-key": ("patch_storage.azure_account_key", "LP_PATCH_STORAGE_AZURE_ACCOUNT_KEY"),
        "connection-string": (
            "patch_storage.azure_connection_string",
            "LP_PATCH_STORAGE_AZURE_CONNECTION_STRING",
        ),
        "client-secret": ("patch_storage.azure_client_secret", "LP_PATCH_STORAGE_AZURE_CLIENT_SECRET"),
    },
}


def _encode(value: object) -> str:
    """Encode a Python config value the way the Go server's viper config expects it.

    Args:
        value: the config value to encode.

    Returns:
        A string representation suitable for an `LP_*` environment variable.
    """
    if isinstance(value, dict):
        value = value.get(SECRET_VALUE_KEY, "")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _resolve_secret_config(
    charm: ops.CharmBase, key: str, content_key: str = SECRET_VALUE_KEY
) -> typing.Optional[str]:
    """Resolve a `type: secret` config option to a given key in its content.

    Args:
        charm: the charm to read the config option from.
        key: the config option name.
        content_key: the secret content key to read.

    Returns:
        The `content_key` content, or None if the option is unset or the
        secret doesn't provide that key.

    Raises:
        CharmConfigInvalidError: if the option is set to a secret URI that is
            inaccessible (as opposed to simply unset).
    """
    try:
        value = config_get_with_secret(charm, key)
    except ops.SecretNotFoundError as exc:
        raise CharmConfigInvalidError(
            f"could not access the secret configured for `{key}`; run "
            f"`juju grant-secret <secret> {charm.app.name}` and try again."
        ) from exc
    if isinstance(value, ops.Secret):
        try:
            return value.get_content(refresh=True).get(content_key)
        except ops.SecretNotFoundError as exc:
            raise CharmConfigInvalidError(
                f"could not access the secret configured for `{key}`; run "
                f"`juju grant-secret <secret> {charm.app.name}` and try again."
            ) from exc
    return typing.cast(typing.Optional[str], value)


class LivepatchApp(paas_charm.app.App):
    """Application wrapper that maps charm state to livepatch-server's `LP_*` env vars."""

    def __init__(self, *, charm: "LivepatchCharm", **kwargs: typing.Any) -> None:
        """Construct, keeping a reference to the owning charm for the custom env var logic.

        `paas_charm.app.App` doesn't keep a reference to the charm itself, but
        `gen_environment()` below needs one to read custom relation/secret state.

        Args:
            charm: the charm instance that owns this App.
            kwargs: passthrough to `paas_charm.app.App.__init__`.
        """
        super().__init__(**kwargs)
        self._charm = charm

    def gen_environment(self) -> dict[str, str]:
        """Return the environment for the Livepatch Pebble service.

        Calls the base implementation first to retain framework-managed
        integration env vars (notably `POSTGRESQL_DB_CONNECT_STRING`, which
        `migrate.sh` reads), then overlays the `LP_*` variables the Go server
        expects, ported from the pre-12-factor charm's `get_env_vars()`.

        Returns:
            A dictionary of environment variable names to their values.
        """
        env = super().gen_environment()

        charm = self._charm

        user_defined_config = dict(self._charm_state.user_defined_config)

        # Expand credential-group secrets into their individual `LP_*` env
        # vars, falling back to each sub-key's deprecated plain-text option
        # when the secret doesn't provide it, before the generic transform runs.
        group_env: dict[str, str] = {}
        for group_key, sub_keys in SECRET_GROUP_ENV_VARS.items():
            content = user_defined_config.pop(group_key, None)
            for content_key, (plain_key, env_name) in sub_keys.items():
                plain_value = user_defined_config.pop(plain_key, None)
                value = content.get(content_key) if isinstance(content, dict) else None
                if value is None:
                    value = plain_value
                if value:
                    group_env[env_name] = _encode(value)

        # Same idea for standalone options with a preferred secret sibling. A
        # configured secret missing the `value` key is a misconfiguration, not
        # an absence, so it must not silently fall back to the plaintext option.
        for plain_key, (secret_key, env_name) in SECRET_FALLBACK_ENV_VARS.items():
            secret_content = user_defined_config.pop(secret_key, None)
            plain_value = user_defined_config.pop(plain_key, None)
            if isinstance(secret_content, dict):
                value = secret_content.get(SECRET_VALUE_KEY)
                if value is None:
                    raise CharmConfigInvalidError(
                        f"the secret configured for `{secret_key}` must have a `{SECRET_VALUE_KEY}` key"
                    )
            else:
                value = plain_value
            if value:
                group_env[env_name] = _encode(value)

        lp_env: dict[str, str] = {
            "LP_" + k.replace("-", "_").replace(".", "_").upper(): _encode(v)
            for k, v in user_defined_config.items()
        }
        lp_env.update(group_env)
        lp_env["LP_SERVER_IS_LEADER"] = _encode(charm.unit.is_leader())
        lp_env["LIVEPATCH_CONFIG_LOCATION"] = "/etc/livepatch.yaml"
        lp_env["LP_SERVER_SERVER_ADDRESS"] = f":{self._workload_config.port}"

        if "POSTGRESQL_DB_CONNECT_STRING" in env:
            lp_env["LP_DATABASE_CONNECTION_STRING"] = env["POSTGRESQL_DB_CONNECT_STRING"]
        else:
            legacy_dsn = charm.legacy_database_connection_string()
            if legacy_dsn:
                lp_env["LP_DATABASE_CONNECTION_STRING"] = legacy_dsn

        metrics_dsn = charm.metrics_db_connection_string()
        if metrics_dsn:
            lp_env["LP_TIMESCALE_DB_CONNECTION_STRING"] = metrics_dsn

        airgapped_address = charm.pro_airgapped_server_address()
        if airgapped_address:
            lp_env["LP_CONTRACTS_ENABLED"] = _encode(True)
            lp_env["LP_CONTRACTS_URL"] = airgapped_address
            if not lp_env.get("LP_PATCH_SYNC_TOKEN"):
                lp_env["LP_PATCH_SYNC_ENABLED"] = _encode(False)
        else:
            if not lp_env.get("LP_PATCH_SYNC_TOKEN"):
                lp_env["LP_PATCH_SYNC_TOKEN"] = charm.resource_token() or ""
            if lp_env.get("LP_PATCH_SYNC_ENABLED") == "true":
                lp_env["LP_PATCH_SYNC_ID"] = charm.model.uuid

        cve_service_address = charm.cve_catalog_address()
        if cve_service_address and charm.unit.is_leader():
            lp_env["LP_CVE_SYNC_SOURCE_URL"] = cve_service_address
            lp_env["LP_LSN_SYNC_SOURCE_URL"] = cve_service_address

        if lp_env.get("LP_PATCH_STORAGE_TYPE") == "postgres" and not lp_env.get(
            "LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"
        ):
            lp_env["LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"] = env.get(
                "POSTGRESQL_DB_CONNECT_STRING", ""
            )

        # Remap go-framework's generic OTLP tracing env vars (from the
        # `tracing` integration) to the server's expected LP_TRACING_* names.
        otlp_endpoint = env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            parsed = urlparse(otlp_endpoint)
            lp_env["LP_TRACING_OTLP_ENDPOINT"] = parsed.netloc or otlp_endpoint
            lp_env["LP_TRACING_PROTOCOL"] = "http"
            lp_env["LP_TRACING_INSECURE"] = _encode(parsed.scheme == "http")

        otel_metrics = charm.otel_metrics_endpoint()
        if otel_metrics:
            endpoint, protocol, insecure = otel_metrics
            lp_env["LP_OTEL_METRICS_OTLP_ENDPOINT"] = endpoint
            lp_env["LP_OTEL_METRICS_PROTOCOL"] = protocol
            lp_env["LP_OTEL_METRICS_INSECURE"] = _encode(insecure)

        env.update({k: v for k, v in lp_env.items() if v not in ("", None)})
        return env


class LivepatchCharm(paas_charm.go.Charm):
    """Livepatch k8s charm entrypoint."""

    def __init__(self, *args: typing.Any) -> None:
        """Initialise the charm, custom relations and custom actions.

        Args:
            args: passthrough to CharmBase/PaasCharm.
        """
        # The `metrics-db`/`postgresql` interface collision in make_database_requirers()
        # (see module-level patch above) is handled before this call, so no special
        # metadata juggling is needed here anymore.
        super().__init__(*args)
        setup_log_redaction()

        # Cache the configured ingress method so `_update_ingress_method` is
        # idempotent (matches the pre-12-factor charm's behaviour).
        self._configured_ingress: typing.Optional[str] = None
        self._nginx_route_requirer: typing.Optional[object] = None
        self._update_ingress_method()
        self.framework.observe(self.on.config_changed, self._on_config_changed_ingress)

        # Legacy (pgsql interface) database support, re-added for backward
        # compatibility alongside the modern `postgresql` relation. Only one
        # of the two may be related at a time.
        self.legacy_db = pgsql.PostgreSQLClient(self, DATABASE_LEGACY_RELATION)
        self.framework.observe(
            self.legacy_db.on.database_relation_joined, self._on_legacy_db_relation_joined
        )
        self.framework.observe(self.legacy_db.on.master_changed, self._on_legacy_db_master_changed)
        self.framework.observe(
            self.legacy_db.on.standby_changed, self._on_legacy_db_standby_changed
        )
        self.framework.observe(
            self.on[DATABASE_LEGACY_RELATION].relation_broken, self._on_legacy_db_relation_broken
        )

        self._metrics_db = PaaSDatabaseRequires(
            self, relation_name=METRICS_DB_RELATION, database_name=METRICS_DB_NAME
        )
        self.framework.observe(self._metrics_db.on.database_created, self._on_metrics_db_changed)
        self.framework.observe(self._metrics_db.on.endpoints_changed, self._on_metrics_db_changed)
        self.framework.observe(
            self.on[METRICS_DB_RELATION].relation_broken, self._on_metrics_db_broken
        )

        self.framework.observe(
            self.on[PRO_AIRGAPPED_SERVER_RELATION].relation_changed, self._on_reconcile
        )
        self.framework.observe(
            self.on[PRO_AIRGAPPED_SERVER_RELATION].relation_departed, self._on_reconcile
        )
        self.framework.observe(self.on[CVE_CATALOG_RELATION].relation_changed, self._on_reconcile)
        self.framework.observe(self.on[CVE_CATALOG_RELATION].relation_broken, self._on_reconcile)
        self.framework.observe(self.on[SEND_OTLP_RELATION].relation_changed, self._on_reconcile)
        self.framework.observe(self.on[SEND_OTLP_RELATION].relation_broken, self._on_reconcile)

        self.framework.observe(self.on.get_resource_token_action, self._on_get_resource_token)
        self.framework.observe(self.on.schema_upgrade_action, self._on_schema_upgrade)
        self.framework.observe(self.on.schema_version_action, self._on_schema_version)
        self.framework.observe(self.on.restart_action, self._on_restart_action)
        self.framework.observe(self.on.emit_updated_config_action, self._on_emit_updated_config)

    def restart(self, rerun_migrations: bool = False) -> None:
        """Reconfigure the workload, refreshing trusted CA certs first.

        Args:
            rerun_migrations: whether it is necessary to run the migrations again.
        """
        if self._container.can_connect():
            try:
                self._update_trusted_ca_certs(self._container)
            except CharmConfigInvalidError as exc:
                self.update_app_and_unit_status(ops.BlockedStatus(str(exc)))
                return
        super().restart(rerun_migrations=rerun_migrations)

    def _create_app(self) -> paas_charm.app.App:
        """Build the App instance used to configure the Pebble service.

        Returns:
            A new LivepatchApp instance.
        """
        return LivepatchApp(
            charm=self,
            container=self._container,
            charm_state=self._create_charm_state(),
            workload_config=self._workload_config,
            database_migration=self._database_migration,
        )

    # --- Custom relation handlers -------------------------------------------------

    def _on_metrics_db_changed(self, _: DatabaseRequiresEvent) -> None:
        """Handle metrics-db database-created/endpoints-changed events."""
        self.restart(rerun_migrations=True)

    def _on_metrics_db_broken(self, _: ops.RelationBrokenEvent) -> None:
        """Handle metrics-db relation-broken event."""
        self.restart()

    def _on_reconcile(self, _: ops.HookEvent) -> None:
        """Reconfigure the workload in response to a custom relation event."""
        self.restart()

    def _on_config_changed_ingress(self, _: ops.ConfigChangedEvent) -> None:
        """Re-evaluate the selected ingress method on config-changed."""
        self._update_ingress_method()

    def _update_ingress_method(self) -> None:
        """Configure the legacy nginx-route ingress if selected via config.

        This is idempotent and safe to call on every config-changed event, in
        addition to go-framework's own always-on `ingress` (traefik) requires.
        """
        ingress_method = self.config.get("ingress-interface")
        if ingress_method != "legacy-nginx-route":
            return
        if self._configured_ingress == "legacy-nginx-route":
            return
        # The returned requirer must be kept alive on the charm instance: the
        # library's own provider side keeps a reference for the same reason,
        # and an unreferenced requirer can be garbage-collected before it
        # finishes handling relation events.
        self._nginx_route_requirer = require_nginx_route(
            charm=self,
            service_hostname=self.app.name,
            service_name=self.app.name,
            service_port=self._workload_config.port,
        )
        self._configured_ingress = "legacy-nginx-route"

    # --- Legacy (pgsql interface) database handlers ---------------------------

    def _on_legacy_db_relation_joined(self, event: pgsql.DatabaseRelationJoinedEvent) -> None:
        """Handle the legacy database relation being joined."""
        if self.model.get_relation("postgresql"):
            # Raising here would crash the hook and leave the unit in error;
            # surface the conflict as a blocked status instead.
            self.unit.status = ops.BlockedStatus(
                "cannot relate to both `postgresql` and `database-legacy`; remove one"
            )
            return
        if self.unit.is_leader():
            event.database = DATABASE_LEGACY_NAME
        elif event.database != DATABASE_LEGACY_NAME:
            event.defer()

    def _on_legacy_db_master_changed(self, event: pgsql.MasterChangedEvent) -> None:
        """Handle the legacy database's primary unit connection info changing."""
        if event.database != DATABASE_LEGACY_NAME:
            return
        if not self.unit.is_leader():
            return
        # wokeignore:rule=master
        if event.master is None:
            return
        # Query parameters can cause connection errors downstream; keep only
        # the base connection string, matching the pre-12-factor charm.
        # wokeignore:rule=master
        db_uri = event.master.uri.split("?", 1)[0]
        try:
            secret = self.model.get_secret(label=LEGACY_DB_SECRET_LABEL)
            secret.set_content({"dsn": db_uri})
        except ops.SecretNotFoundError:
            self.app.add_secret({"dsn": db_uri}, label=LEGACY_DB_SECRET_LABEL)
        self.restart(rerun_migrations=True)

    def _on_legacy_db_standby_changed(self, _: pgsql.StandbyChangedEvent) -> None:
        """Handle legacy database standby units changing. Currently a no-op."""
        return

    def _on_legacy_db_relation_broken(self, _: ops.RelationBrokenEvent) -> None:
        """Handle the legacy database relation being broken."""
        # Only the leader owns (and may remove) this app-owned secret; other
        # units would fail the removal and could otherwise keep serving a
        # stale DSN if cleanup errored out.
        if self.unit.is_leader():
            try:
                secret = self.model.get_secret(label=LEGACY_DB_SECRET_LABEL)
                secret.remove_all_revisions()
            except ops.SecretNotFoundError:
                pass
        self.restart()

    def legacy_database_connection_string(self) -> typing.Optional[str]:
        """Return the legacy (pgsql interface) database connection string, if any.

        Returns:
            The connection string, or None if the database-legacy relation is
            gone or no DSN has been stored yet.
        """
        if not self.model.get_relation(DATABASE_LEGACY_RELATION):
            return None
        try:
            secret = self.model.get_secret(label=LEGACY_DB_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return None
        return secret.get_content(refresh=True).get("dsn")

    def _update_trusted_ca_certs(self, container: ops.Container) -> None:
        """Update trusted CA certificates with the cert from configuration.

        Livepatch needs to restart to use newly received certificates, hence
        this is called from `restart()` before the base reconcile runs.

        Note: the go-framework rock's runtime stages the `ca-certificates_data`
        chisel slice (certificate data only), which may not include the
        `update-ca-certificates` script itself (that's part of the full
        `ca-certificates` package). If this exec fails with "executable not
        found", the rock needs `ca-certificates` added to its stage-packages.

        Args:
            container: the workload container; the caller must ensure it can connect.

        Raises:
            CharmConfigInvalidError: if `contracts.credentials-secret` is set
                to an inaccessible secret URI.
        """
        contracts_ca = _resolve_secret_config(self, "contracts.credentials-secret", content_key="ca")
        if contracts_ca is None:
            contracts_ca = self.config.get("contracts.ca")
        if not contracts_ca:
            logger.debug("ca config not set")
            return

        try:
            cert = b64decode(contracts_ca).decode("utf8")
        except Exception:  # noqa: BLE001 - malformed config shouldn't crash the charm
            logger.error("failed to parse base64 value of the `ca` key in `contracts.credentials-secret`/`contracts.ca`")
            return

        container.push(TRUSTED_CA_FILENAME, cert, make_dirs=True)
        try:
            stdout, stderr = container.exec(["update-ca-certificates", "--fresh"]).wait_output()
            logger.info("stdout update-ca-certificates: %s", stdout)
            logger.info("stderr update-ca-certificates: %s", stderr)
        except (ops.pebble.Error, FileNotFoundError) as e:
            # Don't let a missing executable or a non-zero exit abort restart()
            # and leave the unit stuck in error; log and continue instead.
            logger.error("failed to run update-ca-certificates: %s", e)

    def metrics_db_connection_string(self) -> typing.Optional[str]:
        """Return the metrics/timescale database connection string, if available.

        Returns:
            The PostgreSQL connection URI for the metrics-db relation, or None.
        """
        data = self._metrics_db.to_relation_data()
        return data.uris.split(",")[0] if data else None

    def pro_airgapped_server_address(self) -> typing.Optional[str]:
        """Return the pro-airgapped-server address, if related.

        Uses the first related unit's databag, sorted by unit name for
        consistency across all units of this application.

        Returns:
            The airgapped contracts server base URL, or None.
        """
        relation = self.model.get_relation(PRO_AIRGAPPED_SERVER_RELATION)
        if not relation or not relation.units:
            return None
        for unit in sorted(relation.units, key=lambda u: u.name):
            data = relation.data.get(unit)
            if not data or not data.get("hostname"):
                continue
            scheme = data.get("scheme") or "http"
            port = data.get("port")
            netloc = data["hostname"] + (f":{port}" if port else "")
            return f"{scheme}://{netloc}"
        return None

    def cve_catalog_address(self) -> typing.Optional[str]:
        """Return the Livepatch CVE service address, if related.

        Returns:
            The CVE catalog service URL, or None.
        """
        relation = self.model.get_relation(CVE_CATALOG_RELATION)
        if not relation:
            return None
        address = relation.data.get(relation.app, {}).get("url", "")
        return address or None

    def otel_metrics_endpoint(self) -> typing.Optional[typing.Tuple[str, str, bool]]:
        """Return (endpoint, protocol, insecure) for the send-otlp relation, if related.

        Returns:
            A tuple of (endpoint, protocol, insecure), or None.
        """
        relation = self.model.get_relation(SEND_OTLP_RELATION)
        if not relation:
            return None
        data = relation.data.get(relation.app, {})
        endpoint = data.get("endpoint")
        if not endpoint:
            return None
        protocol = data.get("protocol", "http")
        insecure = data.get("insecure", "true") == "true"
        return endpoint, protocol, insecure

    def resource_token(self) -> typing.Optional[str]:
        """Return the stored patch-sync resource token, if any.

        Returns:
            The resource token, or None if not yet set.
        """
        try:
            secret = self.model.get_secret(label=RESOURCE_TOKEN_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return None
        return secret.get_content(refresh=True).get("token")

    # --- Custom actions -------------------------------------------------------

    def _on_get_resource_token(self, event: ops.ActionEvent) -> None:
        """Handle the get-resource-token action: exchange a contract token for a resource token."""
        if not self.unit.is_leader():
            event.fail("cannot fetch the resource token: unit is not the leader")
            return

        if self.config.get("patch-sync.token") or self.config.get("patch-sync.token-secret"):
            event.fail(
                "patch-sync.token/patch-sync.token-secret is already set. It should be unset "
                "before setting a resource token"
            )
            return

        if self.pro_airgapped_server_address():
            event.fail(
                "already integrated with `pro-airgapped-server`. The relation should be "
                "removed before setting a resource token"
            )
            return

        contract_token_secret_id = event.params.get("contract-token-secret", "")
        contract_token = ""
        if contract_token_secret_id:
            try:
                contract_token_secret = self.model.get_secret(id=contract_token_secret_id)
                contract_token = contract_token_secret.get_content(refresh=True).get(SECRET_VALUE_KEY) or ""
            except ops.SecretNotFoundError:
                event.fail(
                    "cannot fetch the resource token: could not access the `contract-token-secret` "
                    f"secret. Run `juju grant-secret <secret> {self.app.name}` and try again."
                )
                return
            if not contract_token:
                event.fail(
                    "cannot fetch the resource token: the secret must have a "
                    f"`{SECRET_VALUE_KEY}` key with the contract token"
                )
                return
        else:
            contract_token = event.params.get("contract-token", "")

        if not contract_token:
            event.fail(
                "cannot fetch the resource token: no contract token provided (use "
                "`contract-token-secret` (preferred) or `contract-token`)"
            )
            return

        proxies = utils.get_proxy_dict(self.config)
        contracts_url = self.config.get("contracts.url", "")
        machine_token = utils.get_machine_token(
            contract_token, contracts_url=contracts_url, proxies=proxies
        )
        if not machine_token:
            event.fail("cannot fetch the resource token: failed to fetch the machine token")
            return

        resource_token = utils.get_resource_token(
            machine_token, contracts_url=contracts_url, proxies=proxies
        )
        if not resource_token:
            event.fail("cannot fetch the resource token: failed to fetch the resource token")
            return

        try:
            secret = self.model.get_secret(label=RESOURCE_TOKEN_SECRET_LABEL)
            secret.set_content({"token": resource_token})
        except ops.SecretNotFoundError:
            self.app.add_secret({"token": resource_token}, label=RESOURCE_TOKEN_SECRET_LABEL)

        self.restart()
        event.set_results({"result": "resource token set"})

    def _on_schema_version(self, event: ops.ActionEvent) -> None:
        """Handle the schema-version action: report whether migrations are pending."""
        status = self._database_migration.get_status()
        event.set_results({"status": status.value})

    def _on_schema_upgrade(self, event: ops.ActionEvent) -> None:
        """Handle the schema-upgrade action: run schema migrations for all connected databases.

        This execs `livepatch-schema-tool` directly in the workload container
        (schema-tool is bundled in the same rock/container as the server now,
        unlike the pre-12-factor charm's separate sidecar container), for
        manual/on-demand testing independent of the automatic migration the
        framework runs via `migrate.sh` on relevant relation events.
        """
        container = self._container
        if not container.can_connect():
            event.fail("schema migration failed: cannot connect to the workload container")
            return

        env = self._create_app().gen_environment()
        main_dsn = env.get("POSTGRESQL_DB_CONNECT_STRING") or self.legacy_database_connection_string()
        if not main_dsn:
            event.fail("schema migration failed: database connection not set/ready")
            return

        targets = {"livepatchdb": main_dsn}
        metrics_dsn = self.metrics_db_connection_string()
        if metrics_dsn:
            targets["timescaledb"] = metrics_dsn

        if not container.exists(SCHEMA_TOOL_PATH):
            event.fail("schema migration failed: schema tool not found in the workload container")
            return

        for target, conn_str in targets.items():
            try:
                process = container.exec(
                    [SCHEMA_TOOL_PATH, "upgrade", "--db", conn_str, "--target", target],
                )
                stdout, _ = process.wait_output()
                logger.info(stdout)
            except Exception as e:  # noqa: BLE001 - logged server-side only, see below
                # The exception (e.g. ops.pebble.ExecError) may embed the exec
                # argv, which contains the DSN/credentials passed via
                # `--db <conn_str>`. Log it server-side only; return a
                # sanitized message to the action caller.
                logger.error("schema migration failed for %s database: %s", target, e)
                event.fail(f"schema migration failed for {target} database; see charm logs for details")
                return

        event.set_results({"result": "schema migration completed"})

    def _on_restart_action(self, event: ops.ActionEvent) -> None:
        """Handle the restart action: reconfigure and restart the workload."""
        self.restart()
        event.set_results({"result": "restarted"})

    def _on_emit_updated_config(self, event: ops.ActionEvent) -> None:
        """Handle the emit-updated-config action: convert legacy reactive charm config."""
        import yaml

        config_content = event.params["config-file"]
        try:
            config_yaml = yaml.safe_load(config_content)
            if not (isinstance(config_yaml, dict) and config_yaml.keys()):
                event.fail(f"invalid config file format. Got content {config_content}")
                return
        except yaml.YAMLError as e:
            event.fail(f"Failed to parse YAML: {e}")
            return
        try:
            result = map_old_config_to_new_config(config_yaml)
        except ValueError as e:
            event.fail(f"Failed to map old config to new config: {e}")
            return
        event.set_results({"result": result})


if __name__ == "__main__":
    ops.main(LivepatchCharm)
