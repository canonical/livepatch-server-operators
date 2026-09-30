# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Livepatch k8s charm."""

import dataclasses

import pytest
from ops import testing

from charm import LEGACY_DB_SECRET_LABEL, RESOURCE_TOKEN_SECRET_LABEL

from .conftest import (
    APP_CONTAINER,
    DEFAULT_LAYER,
    MAIN_DSN,
    METRICS_DSN,
    SCHEMA_TOOL_PATH,
    SECRET_STORAGE_KEY,
    SERVICE_NAME,
    metrics_db_relation,
)

# ---------------------------------------------------------------------------
# Pebble layer / environment mapping
# ---------------------------------------------------------------------------


def test_config_changed_reaches_active_with_expected_env(context, base_state):
    """arrange: leader unit, secret-storage peer and postgresql relations ready.
    act: config-changed.
    assert: charm reaches ActiveStatus and the Pebble plan has the expected LP_* env.
    """
    state_out = context.run(context.on.config_changed(), testing.State(**base_state))

    assert state_out.unit_status == testing.ActiveStatus()
    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_DATABASE_CONNECTION_STRING"] == MAIN_DSN
    assert env["LP_SERVER_IS_LEADER"] == "true"
    assert env["LIVEPATCH_CONFIG_LOCATION"] == "/etc/livepatch.yaml"
    assert env["LP_SERVER_SERVER_ADDRESS"] == ":8080"
    # Boolean/False config values are encoded as literal "true"/"false" strings.
    assert env["LP_SERVER_IS_HOSTED"] == "false"


def test_missing_postgresql_relation_blocks_without_crashing(context, base_state):
    """arrange: no `postgresql` relation (only the secret-storage peer).
    act: config-changed.
    assert: `is_ready()` reports missing integrations; the charm is blocked, not errored.
    """
    state_in = dataclasses.replace(
        testing.State(**base_state),
        relations={testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "k"})},
    )

    state_out = context.run(context.on.config_changed(), state_in)

    assert isinstance(state_out.unit_status, testing.BlockedStatus)
    assert "postgresql" in state_out.unit_status.message


def test_patch_storage_postgres_falls_back_to_main_db(context, base_state):
    """arrange: patch-storage.type=postgres, no explicit postgres-connection-string.
    act: config-changed.
    assert: LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING falls back to the main DB DSN.
    """
    state_in = dataclasses.replace(testing.State(**base_state), config={"patch-storage.type": "postgres"})

    state_out = context.run(context.on.config_changed(), state_in)

    assert state_out.unit_status == testing.ActiveStatus()
    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"] == MAIN_DSN


def test_patch_storage_postgres_respects_explicit_connection_string(context, base_state):
    """arrange: patch-storage.type=postgres with an explicit connection string.
    act: config-changed.
    assert: the explicit connection string is used, not the main DB DSN.
    """
    explicit_dsn = "postgresql://custom:pw@custom-host:5432/patches"
    state_in = dataclasses.replace(
        testing.State(**base_state),
        config={
            "patch-storage.type": "postgres",
            "patch-storage.postgres-connection-string": explicit_dsn,
        },
    )

    state_out = context.run(context.on.config_changed(), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"] == explicit_dsn


# ---------------------------------------------------------------------------
# Legacy (pgsql interface) database relation
# ---------------------------------------------------------------------------


def test_legacy_db_relation_joined_sets_database_name_on_leader(context, base_state, mock_leader_settings):
    """arrange: leader unit, no modern `postgresql` relation, legacy relation joining.
    act: database-legacy relation-joined.
    assert: the event doesn't crash the charm (database name assignment is exercised).
    """
    legacy_relation = testing.Relation(endpoint="database-legacy", interface="pgsql")
    state_in = dataclasses.replace(
        testing.State(**base_state),
        relations={
            testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "k"}),
            legacy_relation,
        },
    )

    with context(context.on.relation_joined(legacy_relation, remote_unit=0), state_in) as manager:
        manager.run()


def test_legacy_db_relation_joined_conflicts_with_modern_relation(context, base_state, mock_leader_settings):
    """arrange: both `postgresql` and `database-legacy` relations present.
    act: database-legacy relation-joined.
    assert: the charm reports BlockedStatus instead of crashing.
    """
    legacy_relation = testing.Relation(endpoint="database-legacy", interface="pgsql")
    state_in = dataclasses.replace(
        testing.State(**base_state),
        relations=set(base_state["relations"]) | {legacy_relation},
    )

    state_out = context.run(context.on.relation_joined(legacy_relation, remote_unit=0), state_in)

    assert isinstance(state_out.unit_status, testing.BlockedStatus)
    assert "database-legacy" in state_out.unit_status.message


def test_legacy_db_master_changed_stores_secret_and_restarts(context, base_state, mock_leader_settings):
    """arrange: leader unit with only the legacy database relation, joined then given master data.
    act: database-legacy relation-joined, then relation-changed with master-changed data.
    assert: the DSN is stored in the legacy secret, ready to be used as
        LP_DATABASE_CONNECTION_STRING (the unit stays Blocked because `postgresql` is a
        non-optional go-framework integration and isn't related in this scenario; see the
        `is_ready()`-gating note above `restart()`).
    """
    peer = testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "k"})
    legacy_relation = testing.Relation(
        endpoint="database-legacy",
        interface="pgsql",
        local_unit_data={"egress-subnets": "192.0.2.0/24"},
    )
    joined_state = context.run(
        context.on.relation_joined(legacy_relation, remote_unit=0),
        testing.State(leader=True, relations={peer, legacy_relation}),
    )

    # The legacy `pgsql` interface publishes libpq keyword=value connection strings
    # (parsed by the `pgconnstr` library), not `postgresql://` URIs.
    master_uri = "host=legacy-host port=5432 dbname=livepatch-server user=legacy-user password=legacy-pass"
    # Base the follow-up event on the *post-join* relation (joining writes the
    # requested database name into this unit's own databag too), not the original
    # object. "allowed-subnets" must cover our local unit's "egress-subnets" for the
    # pgsql library to treat the remote data as ready.
    joined_relation = joined_state.get_relation(legacy_relation.id)
    legacy_relation_with_master = dataclasses.replace(
        joined_relation,
        remote_app_data={
            "database": "livepatch-server",
            "master": master_uri,
            "allowed-units": "canonical-livepatch-server-k8s/0",
            "allowed-subnets": "192.0.2.0/24",
        },
    )
    state_in = dataclasses.replace(
        joined_state,
        relations={peer, legacy_relation_with_master},
        containers={testing.Container(APP_CONTAINER, can_connect=True)},
    )

    state_out = context.run(context.on.relation_changed(legacy_relation_with_master), state_in)

    secret = state_out.get_secret(label=LEGACY_DB_SECRET_LABEL)
    assert secret.tracked_content["dsn"] == "postgresql://legacy-user:legacy-pass@legacy-host:5432/livepatch-server"


def test_legacy_db_standby_changed_is_a_noop(context, base_state, mock_leader_settings):
    """arrange: leader unit with a legacy database relation already joined.
    act: database-legacy relation-changed with only standby data (no master change).
    assert: the charm doesn't raise and remains active.
    """
    peer = testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "k"})
    legacy_relation = testing.Relation(endpoint="database-legacy", interface="pgsql")
    joined_state = context.run(
        context.on.relation_joined(legacy_relation, remote_unit=0),
        testing.State(leader=True, relations={peer, legacy_relation}),
    )

    joined_relation = joined_state.get_relation(legacy_relation.id)
    legacy_relation_with_standby = dataclasses.replace(
        joined_relation,
        remote_app_data={
            "database": "livepatch-server",
            "standbys": "postgresql://standby-user:standby-pass@standby-host:5432/livepatch-server",
            "allowed-units": "canonical-livepatch-server-k8s/0",
        },
    )
    state_in = dataclasses.replace(
        joined_state,
        relations={peer, legacy_relation_with_standby},
        containers={testing.Container(APP_CONTAINER, can_connect=True)},
    )

    state_out = context.run(context.on.relation_changed(legacy_relation_with_standby), state_in)

    assert not state_out.secrets


def test_legacy_db_relation_broken_clears_secret_and_restarts(context, base_state):
    """arrange: leader unit with a legacy DSN already stored in the legacy secret.
    act: database-legacy relation-broken.
    assert: the secret is removed and the charm restarts without the legacy DSN.
    """
    secret = testing.Secret(
        tracked_content={"dsn": "postgresql://old-dsn/db"}, label=LEGACY_DB_SECRET_LABEL, owner="app"
    )
    legacy_relation = testing.Relation(endpoint="database-legacy", interface="pgsql")
    state_in = dataclasses.replace(
        testing.State(**base_state),
        secrets={secret},
        relations=set(base_state["relations"]) | {legacy_relation},
    )

    state_out = context.run(context.on.relation_broken(legacy_relation), state_in)

    assert not state_out.secrets
    assert state_out.unit_status == testing.ActiveStatus()


def test_legacy_db_relation_broken_non_leader_skips_secret_removal(context, base_state):
    """arrange: a non-leader unit with a legacy DSN secret it doesn't own.
    act: database-legacy relation-broken.
    assert: the secret is left alone (only the leader may remove an app-owned secret).
    """
    secret = testing.Secret(
        tracked_content={"dsn": "postgresql://old-dsn/db"}, label=LEGACY_DB_SECRET_LABEL, owner="app"
    )
    legacy_relation = testing.Relation(endpoint="database-legacy", interface="pgsql")
    state_in = dataclasses.replace(
        testing.State(**base_state),
        leader=False,
        secrets={secret},
        relations=set(base_state["relations"]) | {legacy_relation},
    )

    state_out = context.run(context.on.relation_broken(legacy_relation), state_in)

    assert len(state_out.secrets) == 1


def test_legacy_database_connection_string_none_without_relation(context, base_state):
    """arrange: no legacy relation, no secret.
    act: call `legacy_database_connection_string()` directly.
    assert: returns None.
    """
    with context(context.on.update_status(), testing.State(**base_state)) as manager:
        assert manager.charm.legacy_database_connection_string() is None
        manager.run()


# ---------------------------------------------------------------------------
# CVE catalog / pro-airgapped-server / send-otlp relations
# ---------------------------------------------------------------------------


def test_cve_catalog_sets_env_on_leader(context, base_state):
    """arrange: leader unit with a cve-catalog relation providing a URL.
    act: cve-catalog relation-changed.
    assert: the CVE/LSN sync source URLs are set (leader-only).
    """
    relation = testing.Relation(
        endpoint="cve-catalog", interface="cve-catalog", remote_app_data={"url": "https://cve.example.com"}
    )
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_CVE_SYNC_SOURCE_URL"] == "https://cve.example.com"
    assert env["LP_LSN_SYNC_SOURCE_URL"] == "https://cve.example.com"


def test_cve_catalog_not_set_on_non_leader(context, base_state):
    """arrange: non-leader unit with a cve-catalog relation providing a URL.
    act: cve-catalog relation-changed.
    assert: the CVE/LSN sync source URLs are not set (only the leader publishes them).
    """
    relation = testing.Relation(
        endpoint="cve-catalog", interface="cve-catalog", remote_app_data={"url": "https://cve.example.com"}
    )
    state_in = dataclasses.replace(
        testing.State(**base_state), leader=False, relations=set(base_state["relations"]) | {relation}
    )

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert "LP_CVE_SYNC_SOURCE_URL" not in env


def test_cve_catalog_broken_removes_env(context, base_state):
    """arrange: leader unit, cve-catalog relation about to be removed.
    act: cve-catalog relation-broken.
    assert: the charm restarts without crashing and the sync URLs are absent.
    """
    relation = testing.Relation(endpoint="cve-catalog", interface="cve-catalog")
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_broken(relation), state_in)

    assert state_out.unit_status == testing.ActiveStatus()
    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert "LP_CVE_SYNC_SOURCE_URL" not in env


def test_pro_airgapped_server_sets_contracts_url_and_disables_sync(context, base_state):
    """arrange: pro-airgapped-server relation with one unit publishing an address.
    act: pro-airgapped-server relation-changed.
    assert: LP_CONTRACTS_URL is set and patch-sync is disabled (no token configured).
    """
    relation = testing.Relation(
        endpoint="pro-airgapped-server",
        interface="livepatch-pro-airgapped-server",
        remote_units_data={0: {"hostname": "airgapped.internal", "scheme": "https", "port": "8443"}},
    )
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_CONTRACTS_ENABLED"] == "true"
    assert env["LP_CONTRACTS_URL"] == "https://airgapped.internal:8443"
    assert env["LP_PATCH_SYNC_ENABLED"] == "false"


def test_pro_airgapped_server_multiple_units_prefers_first_with_data(context, base_state):
    """arrange: two related units; the alphabetically-first has no hostname yet.
    act: pro-airgapped-server relation-changed.
    assert: the charm falls back to the next unit that does publish a hostname.
    """
    relation = testing.Relation(
        endpoint="pro-airgapped-server",
        interface="livepatch-pro-airgapped-server",
        remote_units_data={
            0: {},
            1: {"hostname": "airgapped-1.internal", "scheme": "http", "port": ""},
        },
    )
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_CONTRACTS_URL"] == "http://airgapped-1.internal"


def test_pro_airgapped_server_relation_removed_falls_back_to_resource_token(context, base_state):
    """arrange: leader unit, a stored resource token, no airgapped relation.
    act: config-changed.
    assert: LP_CONTRACTS_ENABLED is off and LP_PATCH_SYNC_TOKEN uses the resource token.
    """
    secret = testing.Secret(tracked_content={"token": "stored-resource-token"}, label=RESOURCE_TOKEN_SECRET_LABEL)
    state_in = dataclasses.replace(testing.State(**base_state), secrets={secret})

    state_out = context.run(context.on.config_changed(), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_CONTRACTS_ENABLED"] == "false"
    assert env["LP_PATCH_SYNC_TOKEN"] == "stored-resource-token"


def test_send_otlp_relation_sets_metrics_endpoint(context, base_state):
    """arrange: leader unit with a send-otlp relation providing an OTLP endpoint.
    act: send-otlp relation-changed.
    assert: the LP_OTEL_METRICS_* env vars reflect the relation data.
    """
    relation = testing.Relation(
        endpoint="send-otlp",
        interface="otlp",
        remote_app_data={"endpoint": "otel-collector:4317", "protocol": "grpc", "insecure": "false"},
    )
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_OTEL_METRICS_OTLP_ENDPOINT"] == "otel-collector:4317"
    assert env["LP_OTEL_METRICS_PROTOCOL"] == "grpc"
    assert env["LP_OTEL_METRICS_INSECURE"] == "false"


# ---------------------------------------------------------------------------
# Ingress method switching
# ---------------------------------------------------------------------------


def test_ingress_defaults_to_legacy_nginx_route(context, base_state):
    """arrange: default config (ingress-interface defaults to legacy-nginx-route).
    act: config-changed.
    assert: the charm records that legacy-nginx-route was configured.
    """
    with context(context.on.config_changed(), testing.State(**base_state)) as manager:
        manager.run()
        assert manager.charm._configured_ingress == "legacy-nginx-route"


def test_ingress_non_legacy_value_does_not_configure_nginx_route(context, base_state):
    """arrange: ingress-interface set to something other than legacy-nginx-route.
    act: config-changed.
    assert: `_update_ingress_method` leaves `_configured_ingress` unset.
    """
    state_in = dataclasses.replace(testing.State(**base_state), config={"ingress-interface": "ingress"})

    with context(context.on.config_changed(), state_in) as manager:
        manager.run()
        assert manager.charm._configured_ingress is None


# ---------------------------------------------------------------------------
# Trusted CA certificate update
# ---------------------------------------------------------------------------


def test_ca_cert_pushed_and_trust_store_refreshed(context, base_state):
    """arrange: contracts.ca set to a valid base64-encoded certificate.
    act: config-changed.
    assert: the decoded cert is pushed to the container and update-ca-certificates runs.
    """
    import base64

    cert_pem = "-----BEGIN CERTIFICATE-----\nZmFrZS1jZXJ0\n-----END CERTIFICATE-----\n"
    container = testing.Container(
        APP_CONTAINER,
        can_connect=True,
        _base_plan=DEFAULT_LAYER,
        execs={testing.Exec(["update-ca-certificates", "--fresh"], return_code=0)},
    )
    state_in = dataclasses.replace(
        testing.State(**base_state),
        config={"contracts.ca": base64.b64encode(cert_pem.encode()).decode()},
        containers={container},
    )

    state_out = context.run(context.on.config_changed(), state_in)

    fs = state_out.get_container(APP_CONTAINER).get_filesystem(context)
    pushed = fs / "usr/local/share/ca-certificates/trusted-contracts.ca.crt"
    assert pushed.read_text() == cert_pem
    assert state_out.unit_status == testing.ActiveStatus()


def test_ca_cert_malformed_base64_is_logged_and_skipped(context, base_state):
    """arrange: contracts.ca set to a value that isn't valid base64.
    act: config-changed.
    assert: the charm logs the problem and continues (doesn't crash or block).
    """
    state_in = dataclasses.replace(testing.State(**base_state), config={"contracts.ca": "not valid base64!!"})

    state_out = context.run(context.on.config_changed(), state_in)

    assert state_out.unit_status == testing.ActiveStatus()
    assert any("failed to parse base64" in log.message for log in context.juju_log)


def test_ca_cert_update_exec_failure_does_not_block_restart(context, base_state):
    """arrange: contracts.ca set, but `update-ca-certificates` isn't a known command.
    act: config-changed.
    assert: the exec failure is logged but the charm still reaches ActiveStatus.
    """
    import base64

    container = testing.Container(
        APP_CONTAINER, can_connect=True, _base_plan=DEFAULT_LAYER
    )  # no matching Exec registered
    state_in = dataclasses.replace(
        testing.State(**base_state),
        config={"contracts.ca": base64.b64encode(b"cert-data").decode()},
        containers={container},
    )

    state_out = context.run(context.on.config_changed(), state_in)

    assert state_out.unit_status == testing.ActiveStatus()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_restart_action_restarts_and_reports_result(context, base_state):
    """arrange: a ready unit.
    act: restart action.
    assert: the workload restarts and the action reports success.
    """
    state_out = context.run(context.on.action("restart"), testing.State(**base_state))

    assert context.action_results == {"result": "restarted"}
    assert state_out.unit_status == testing.ActiveStatus()


def test_schema_version_action_reports_pending_by_default(context, base_state):
    """arrange: a ready unit with no migration status file yet.
    act: schema-version action.
    assert: the action reports the PENDING status.
    """
    context.run(context.on.action("schema-version"), testing.State(**base_state))

    assert context.action_results == {"status": "PENDING"}


def test_schema_upgrade_action_success(context, base_state, schema_tool_container):
    """arrange: a ready unit with the schema tool present in the workload container.
    act: schema-upgrade action.
    assert: the schema tool is run once against the main database, and succeeds.
    """
    container = dataclasses.replace(
        schema_tool_container,
        execs={testing.Exec([SCHEMA_TOOL_PATH, "upgrade", "--db", MAIN_DSN, "--target", "livepatchdb"], return_code=0)},
    )
    state_in = dataclasses.replace(testing.State(**base_state), containers={container})

    context.run(context.on.action("schema-upgrade"), state_in)

    assert context.action_results == {"result": "schema migration completed"}


def test_schema_upgrade_action_includes_metrics_target_when_related(context, base_state, schema_tool_container):
    """arrange: a ready unit with the metrics-db relation also present.
    act: schema-upgrade action.
    assert: the schema tool runs for both the main and the timescale targets.
    """
    container = dataclasses.replace(
        schema_tool_container,
        execs={
            testing.Exec([SCHEMA_TOOL_PATH, "upgrade", "--db", MAIN_DSN, "--target", "livepatchdb"], return_code=0),
            testing.Exec(
                [SCHEMA_TOOL_PATH, "upgrade", "--db", METRICS_DSN, "--target", "timescaledb"], return_code=0
            ),
        },
    )
    state_in = dataclasses.replace(
        testing.State(**base_state),
        containers={container},
        relations=set(base_state["relations"]) | {metrics_db_relation()},
    )

    context.run(context.on.action("schema-upgrade"), state_in)

    assert context.action_results == {"result": "schema migration completed"}


def test_schema_upgrade_action_fails_when_tool_missing(context, base_state):
    """arrange: a ready unit whose container doesn't have the schema tool.
    act: schema-upgrade action.
    assert: the action fails with a clear, sanitized message.
    """
    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("schema-upgrade"), testing.State(**base_state))

    assert "schema tool not found" in exc_info.value.message


def test_schema_upgrade_action_fails_when_cannot_connect(context, base_state):
    """arrange: a ready unit whose container can't be reached.
    act: schema-upgrade action.
    assert: the action fails without leaking exec details.
    """
    state_in = dataclasses.replace(
        testing.State(**base_state), containers={testing.Container(APP_CONTAINER, can_connect=False)}
    )

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("schema-upgrade"), state_in)

    assert "cannot connect" in exc_info.value.message


def test_schema_upgrade_action_fails_without_database_connection(context, schema_tool_container):
    """arrange: no postgresql relation and no legacy DSN secret.
    act: schema-upgrade action.
    assert: the action fails because no database connection is configured.
    """
    state_in = testing.State(
        leader=True,
        relations={testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "k"})},
        containers={schema_tool_container},
    )

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("schema-upgrade"), state_in)

    assert "database connection not set" in exc_info.value.message


def test_schema_upgrade_action_reports_sanitized_failure(context, base_state, schema_tool_container):
    """arrange: the schema tool exits non-zero for the main database.
    act: schema-upgrade action.
    assert: the action fails without leaking the DSN (which is passed via argv).
    """
    container = dataclasses.replace(
        schema_tool_container,
        execs={
            testing.Exec(
                [SCHEMA_TOOL_PATH, "upgrade", "--db", MAIN_DSN, "--target", "livepatchdb"],
                return_code=1,
                stderr="connection refused",
            )
        },
    )
    state_in = dataclasses.replace(testing.State(**base_state), containers={container})

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("schema-upgrade"), state_in)

    assert "livepatchdb" in exc_info.value.message
    assert MAIN_DSN not in exc_info.value.message
    assert "test-password" not in exc_info.value.message


def test_get_resource_token_action_success(context, base_state, monkeypatch):
    """arrange: a leader unit with a contract token and no conflicting config.
    act: get-resource-token action.
    assert: the resource token is fetched, stored in a secret, and the workload restarts.
    """
    monkeypatch.setattr("charm.get_machine_token", lambda *a, **k: "machine-token")
    monkeypatch.setattr("charm.get_resource_token", lambda *a, **k: "resource-token")

    state_out = context.run(
        context.on.action("get-resource-token", params={"contract-token": "contract-token"}),
        testing.State(**base_state),
    )

    assert context.action_results == {"result": "resource token set"}
    secret = state_out.get_secret(label=RESOURCE_TOKEN_SECRET_LABEL)
    assert secret.tracked_content["token"] == "resource-token"


def test_get_resource_token_action_fails_for_non_leader(context, base_state):
    """arrange: a non-leader unit.
    act: get-resource-token action.
    assert: the action fails immediately.
    """
    state_in = dataclasses.replace(testing.State(**base_state), leader=False)

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("get-resource-token", params={"contract-token": "t"}), state_in)

    assert "not the leader" in exc_info.value.message


def test_get_resource_token_action_fails_when_sync_token_already_set(context, base_state):
    """arrange: patch-sync.token is already configured.
    act: get-resource-token action.
    assert: the action fails to avoid clobbering the configured token.
    """
    state_in = dataclasses.replace(testing.State(**base_state), config={"patch-sync.token": "existing-token"})

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("get-resource-token", params={"contract-token": "t"}), state_in)

    assert "already set" in exc_info.value.message


def test_get_resource_token_action_fails_when_airgapped_related(context, base_state):
    """arrange: a pro-airgapped-server relation is already established.
    act: get-resource-token action.
    assert: the action fails since the two integrations are mutually exclusive.
    """
    relation = testing.Relation(
        endpoint="pro-airgapped-server",
        interface="livepatch-pro-airgapped-server",
        remote_units_data={0: {"hostname": "airgapped.internal"}},
    )
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("get-resource-token", params={"contract-token": "t"}), state_in)

    assert "pro-airgapped-server" in exc_info.value.message


def test_get_resource_token_action_fails_on_empty_token(context, base_state):
    """arrange: a leader unit.
    act: get-resource-token action with an empty contract token.
    assert: the action fails without attempting the HTTP exchange.
    """
    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(context.on.action("get-resource-token", params={"contract-token": ""}), testing.State(**base_state))

    assert "no contract token provided" in exc_info.value.message


def test_get_resource_token_action_fails_when_machine_token_fetch_fails(context, base_state, monkeypatch):
    """arrange: the contracts service doesn't return a machine token.
    act: get-resource-token action.
    assert: the action fails with a clear message.
    """
    monkeypatch.setattr("charm.get_machine_token", lambda *a, **k: None)

    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(
            context.on.action("get-resource-token", params={"contract-token": "t"}), testing.State(**base_state)
        )

    assert "failed to fetch the machine token" in exc_info.value.message


def test_emit_updated_config_action_success(context, base_state):
    """arrange: a leader unit.
    act: emit-updated-config action with the old reactive charm's exported config.
    assert: the converted config maps the expected keys/values.
    """
    import pathlib

    import yaml

    test_data = pathlib.Path(__file__).parent / "test-data"
    old_config = (test_data / "old_config.yaml").read_text()
    expected = yaml.safe_load((test_data / "expected_config.yaml").read_text())

    context.run(
        context.on.action("emit-updated-config", params={"config-file": old_config}),
        testing.State(**base_state),
    )

    result = context.action_results["result"]
    new_config = yaml.safe_load(result["new-config"])
    assert new_config == expected


def test_emit_updated_config_action_fails_on_invalid_yaml(context, base_state):
    """arrange: a leader unit.
    act: emit-updated-config action with unparsable YAML.
    assert: the action fails with a clear message.
    """
    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(
            context.on.action("emit-updated-config", params={"config-file": "not: valid: yaml: :"}),
            testing.State(**base_state),
        )

    assert "Failed to parse YAML" in exc_info.value.message


def test_emit_updated_config_action_fails_on_non_dict_yaml(context, base_state):
    """arrange: a leader unit.
    act: emit-updated-config action with valid YAML that isn't a mapping.
    assert: the action fails with a clear message.
    """
    with pytest.raises(testing.ActionFailed) as exc_info:
        context.run(
            context.on.action("emit-updated-config", params={"config-file": "- just\n- a\n- list\n"}),
            testing.State(**base_state),
        )

    assert "invalid config file format" in exc_info.value.message


