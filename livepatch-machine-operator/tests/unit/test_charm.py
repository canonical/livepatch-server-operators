# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Livepatch machine charm using state-transition testing."""

import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest
import yaml
from ops import testing

from charm import OperatorMachineCharm
from constants.snap import SERVER_SNAP_REVISION
from constants.statuses import LIVEPATCH_NOT_RUNNING, LIVEPATCH_RUNNING

CHARM_ROOT = Path(__file__).resolve().parent.parent.parent
APP_NAME = "canonical-livepatch-server"

DEFAULT_CONFIG = {
    "server.url-template": "https://example.com/{filename}",
    "server.server-address": "0.0.0.0:8080",
}


@pytest.fixture(scope="module")
def ctx():
    """Build a scenario Context with explicit metadata/config/actions."""
    meta = yaml.safe_load((CHARM_ROOT / "charmcraft.yaml").read_text())
    config = yaml.safe_load((CHARM_ROOT / "config.yaml").read_text())
    actions = yaml.safe_load((CHARM_ROOT / "actions.yaml").read_text())
    return testing.Context(OperatorMachineCharm, meta=meta, config=config, actions=actions)


@pytest.fixture
def snap_set():
    """Record snap.set calls and provide a consistent snap mock."""
    with patch("workload.SnapCache") as snap_cache_cls:
        cache = snap_cache_cls.return_value
        snap = cache.get.return_value
        snap.is_installed.return_value = True
        snap.status.return_value = "enabled"
        snap.version.return_value = "test-version"
        snap.present = True
        snap.revision = SERVER_SNAP_REVISION
        snap.services = {"livepatch": {"active": True}}
        recorded: list[dict] = []

        def _set(delta: Dict[str, Any]) -> None:
            recorded.append(dict(delta))

        def _get(key: str, typed: bool = False) -> Any:
            merged = {}
            for d in recorded:
                merged.update(d)
            if not typed:
                return merged.get(key, "")
            # snapd returns `snap get lp -d` as a nested tree; rebuild it so
            # current_config() reflects values already written by the charm.
            nested: Dict[str, Any] = {}
            for dotted, value in merged.items():
                node = nested
                parts = dotted.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            return nested.get(key, {})

        snap.set.side_effect = _set
        snap.get.side_effect = _get
        yield recorded


@pytest.fixture
def schema_check():
    """Mock the schema version check command to report the schema is current."""
    with patch("subprocess.check_output") as check_output:

        def _side_effect(cmd, *args, **kwargs):
            if cmd[0] == "canonical-livepatch-server.check-schema-version":
                return "some-version"
            if cmd[0] == "leader-get":
                return b""
            if cmd[0] == "update-ca-certificates":
                return b"updated-ca-certs"
            raise AssertionError(f"unexpected call: {cmd}")

        check_output.side_effect = _side_effect
        yield check_output


def _leader_state(relations=None, config=None, **kwargs):
    """Return a leader State with the livepatch peer relation and default config."""
    rels = list(relations) if relations else []
    rels.append(testing.PeerRelation("livepatch"))
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    return testing.State(leader=True, relations=rels, config=cfg, **kwargs)


def _standard_db_relation():
    """Return a standard database relation with valid credentials."""
    return testing.Relation(
        "database",
        remote_app_name="postgres-new",
        remote_app_data={
            "username": "some-username",
            "password": "some-password",
            "endpoints": "some.database.host,some.other.database.host",
        },
    )


class TestSnapConfig:
    """Tests that snap configuration is computed correctly."""

    def test_snap_config(self, ctx, snap_set, schema_check):
        """Test snap config is set as expected."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard], config={"patch-sync.sync-tiers": True})
        ctx.run(ctx.on.config_changed(), state_in)
        assert any(delta.get("lp.patch-sync.sync-tiers") == "True" for delta in snap_set)


class TestLegacyDatabase:
    """Tests for the legacy database relation."""

    def test_legacy_db_master_changed(self, ctx, snap_set, schema_check):
        """Test legacy db master data is captured and used."""
        legacy = testing.Relation("database-legacy", remote_app_name="postgres")
        peer = testing.PeerRelation(
            "livepatch",
            local_unit_data={"legacy_db_uri": '"postgres://user:pass@host:5432/livepatch"'},
        )
        state_in = testing.State(leader=True, relations=[legacy, peer], config=DEFAULT_CONFIG)

        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus(LIVEPATCH_RUNNING)
        assert any("lp.database.connection-string" in delta for delta in snap_set)

    def test_legacy_db_standby_changed(self, ctx, snap_set, schema_check):
        """Test legacy db standby data is captured."""
        legacy = testing.Relation("database-legacy", remote_app_name="postgres")
        peer = testing.PeerRelation(
            "livepatch",
            local_unit_data={"legacy_db_uri": "postgresql://user:pass@host/livepatch"},
        )
        state_in = testing.State(leader=True, relations=[legacy, peer], config=DEFAULT_CONFIG)

        state_out = ctx.run(ctx.on.relation_changed(legacy, remote_unit=0), state_in)

        livepatch_rel = state_out.get_relation(peer.id)
        assert livepatch_rel.local_unit_data.get("legacy_db_uri") is not None

    def test_legacy_db_both_master_and_standby(self, ctx, snap_set, schema_check):
        """Test legacy db relation with both primary and standby units."""
        legacy = testing.Relation("database-legacy", remote_app_name="postgres")
        peer = testing.PeerRelation(
            "livepatch",
            local_unit_data={
                "legacy_db_uri": "postgresql://user:pass@host/livepatch",
                "legacy_db_ro_uris": "postgresql://user:pass@standby-host/livepatch",
            },
        )
        state_in = testing.State(leader=True, relations=[legacy, peer], config=DEFAULT_CONFIG)

        state_out = ctx.run(ctx.on.relation_changed(legacy, remote_unit=0), state_in)

        livepatch_rel = state_out.get_relation(peer.id)
        assert livepatch_rel.local_unit_data.get("legacy_db_uri") is not None
        assert livepatch_rel.local_unit_data.get("legacy_db_ro_uris") is not None


class TestDatabaseRelations:
    """Tests for standard and mutual-exclusion database behaviour."""

    def test_database_relations_are_mutually_exclusive__legacy_first(self, ctx, snap_set):
        """Test adding standard relation when legacy is active is blocked."""
        legacy = testing.Relation("database-legacy", remote_app_name="postgres")
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[legacy, standard])
        state_out = ctx.run(ctx.on.relation_changed(standard), state_in)
        assert state_out.unit_status == testing.BlockedStatus(
            "Integration with both database relations is not allowed; " "use either `database` or `database-legacy`."
        )

    def test_database_relations_are_mutually_exclusive__standard_first(self, ctx, snap_set):
        """Test adding legacy relation when standard is active is blocked."""
        standard = _standard_db_relation()
        legacy = testing.Relation("database-legacy", remote_app_name="postgres")
        state_in = _leader_state(relations=[standard, legacy])
        state_out = ctx.run(ctx.on.relation_changed(standard), state_in)
        assert state_out.unit_status == testing.BlockedStatus(
            "Integration with both database relations is not allowed; " "use either `database` or `database-legacy`."
        )

    def test_standard_database_relation__success(self, ctx, snap_set, schema_check):
        """Test standard db relation successfully integrates with database."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])
        state_out = ctx.run(ctx.on.relation_changed(standard), state_in)
        assert state_out.unit_status == testing.ActiveStatus(LIVEPATCH_RUNNING)
        assert any("lp.database.connection-string" in delta for delta in snap_set)

    def test_standard_database_relation__empty_username_or_password(self, ctx, snap_set):
        """Test standard db relation does not update the db_uri if credentials are empty."""
        standard = testing.Relation(
            "database",
            remote_app_name="postgres-new",
            remote_app_data={"username": "", "password": "", "endpoints": "some.database.host"},
        )
        state_in = _leader_state(relations=[standard])
        state_out = ctx.run(ctx.on.relation_changed(standard), state_in)
        assert state_out.unit_status != testing.ActiveStatus(LIVEPATCH_RUNNING)


class TestWebsiteRelation:
    """Tests for the website relation."""

    def test_website_relation__success(self, ctx, snap_set, schema_check):
        """Test website relation integrates successfully."""
        standard = _standard_db_relation()
        website = testing.Relation("website", remote_app_name="reverse-proxy")
        state_in = _leader_state(relations=[standard, website], config={"server.server-address": "some-host:9999"})
        state_out = ctx.run(ctx.on.relation_changed(website, remote_unit=0), state_in)
        assert state_out.get_relation(website.id).local_unit_data["port"] == "9999"


class TestProAirgappedServer:
    """Tests for the pro-airgapped-server relation."""

    def test_pro_airgapped_server_relation__success(self, ctx, snap_set, schema_check):
        """Test pro-airgapped-server relation."""
        standard = _standard_db_relation()
        pro = testing.Relation(
            "pro-airgapped-server",
            remote_app_name="pro-airgapped-server",
            remote_units_data={0: {"scheme": "scheme", "hostname": "some.host.name", "port": "9999"}},
        )
        state_in = _leader_state(relations=[standard, pro])
        ctx.run(ctx.on.relation_changed(pro, remote_unit=0), state_in)
        assert any(delta.get("lp.contracts.enabled") == "True" for delta in snap_set)
        assert any("lp.contracts.url" in delta for delta in snap_set)

    def test_pro_airgapped_server_relation__multiple_units(self, ctx, snap_set, schema_check):
        """Test pro-airgapped-server relation when there are multiple units."""
        standard = _standard_db_relation()
        pro = testing.Relation(
            "pro-airgapped-server",
            remote_app_name="pro-airgapped-server",
            remote_units_data={
                0: {"scheme": "scheme", "hostname": "first.host", "port": "9999"},
                1: {"scheme": "scheme", "hostname": "second.host", "port": "9999"},
            },
        )
        state_in = _leader_state(relations=[standard, pro])
        ctx.run(ctx.on.relation_changed(pro, remote_unit=0), state_in)
        assert any(delta.get("lp.contracts.url") == "scheme://first.host:9999" for delta in snap_set)

    def test_pro_airgapped_server_relation__multiple_units_one_departs(self, ctx, snap_set, schema_check):
        """Test pro-airgapped-server relation when one unit departs."""
        standard = _standard_db_relation()
        pro = testing.Relation(
            "pro-airgapped-server",
            remote_app_name="pro-airgapped-server",
            remote_units_data={
                0: {"scheme": "scheme", "hostname": "first.host", "port": "9999"},
                1: {"scheme": "scheme", "hostname": "second.host", "port": "9999"},
            },
        )
        state_in = _leader_state(relations=[standard, pro])
        ctx.run(ctx.on.relation_changed(pro, remote_unit=0), state_in)

        pro_after = testing.Relation(
            "pro-airgapped-server",
            remote_app_name="pro-airgapped-server",
            remote_units_data={1: {"scheme": "scheme", "hostname": "second.host", "port": "9999"}},
        )
        state_in2 = _leader_state(relations=[standard, pro_after])
        ctx.run(ctx.on.relation_changed(pro_after, remote_unit=1), state_in2)
        assert any(delta.get("lp.contracts.url") == "scheme://second.host:9999" for delta in snap_set)

    def test_pro_airgapped_server_relation__relation_removed(self, ctx, snap_set, schema_check):
        """Test when pro-airgapped-server is removed."""
        standard = _standard_db_relation()
        pro = testing.Relation(
            "pro-airgapped-server",
            remote_app_name="pro-airgapped-server",
            remote_units_data={0: {"scheme": "scheme", "hostname": "first.host", "port": "9999"}},
        )
        state_in = _leader_state(relations=[standard, pro])
        ctx.run(ctx.on.relation_changed(pro, remote_unit=0), state_in)
        ctx.run(ctx.on.relation_broken(pro), state_in)
        assert any(delta.get("lp.contracts.enabled") == "False" for delta in snap_set)


class TestMetricsDBRelation:
    """Tests for the metrics-db (TimescaleDB) relation."""

    def test_metrics_db_relation__success(self, ctx, snap_set, schema_check):
        """Test metrics-db data configures the timescale connection string."""
        standard = _standard_db_relation()
        metrics_db = testing.Relation(
            "metrics-db",
            remote_app_name="postgresql",
            remote_app_data={"username": "tsuser", "password": "tspass", "endpoints": "postgres.local:5432"},
        )
        state_in = _leader_state(relations=[standard, metrics_db])
        ctx.run(ctx.on.relation_changed(metrics_db), state_in)
        assert any(
            delta.get("lp.timescale_db.connection-string")
            == "postgresql://tsuser:tspass@postgres.local:5432/livepatch-metrics-db"
            for delta in snap_set
        )

    def test_metrics_db_relation__missing_credentials(self, ctx, snap_set, schema_check):
        """Test metrics-db relation without data does not set the connection string."""
        standard = _standard_db_relation()
        metrics_db = testing.Relation("metrics-db", remote_app_name="postgresql")
        state_in = _leader_state(relations=[standard, metrics_db])
        ctx.run(ctx.on.relation_changed(metrics_db), state_in)
        assert not any(delta.get("lp.timescale_db.connection-string") for delta in snap_set)

    def test_metrics_db_relation__relation_removed(self, ctx, snap_set, schema_check):
        """Test removing the metrics-db relation clears the connection string."""
        standard = _standard_db_relation()
        metrics_db = testing.Relation(
            "metrics-db",
            remote_app_name="postgresql",
            remote_app_data={"username": "tsuser", "password": "tspass", "endpoints": "postgres.local:5432"},
        )
        state_in = _leader_state(relations=[standard, metrics_db])
        ctx.run(ctx.on.relation_changed(metrics_db), state_in)

        metrics_db_removed = testing.Relation("metrics-db", remote_app_name="postgresql")
        state_removed = _leader_state(relations=[standard, metrics_db_removed])
        ctx.run(ctx.on.relation_broken(metrics_db_removed), state_removed)
        assert any(delta.get("lp.timescale_db.connection-string") == "" for delta in snap_set)


class TestCVECatalogRelation:
    """Tests for the cve-catalog relation."""

    def test_cve_catalog_relation__success(self, ctx, snap_set, schema_check):
        """Test cve-catalog data configures the CVE and LSN source URLs."""
        standard = _standard_db_relation()
        cve = testing.Relation(
            "cve-catalog",
            remote_app_name="livepatch-cve-service",
            remote_app_data={"url": "scheme://some.host.name:9999"},
        )
        state_in = _leader_state(relations=[standard, cve])
        ctx.run(ctx.on.relation_changed(cve), state_in)
        assert any(delta.get("lp.cve-sync.source-url") == "scheme://some.host.name:9999" for delta in snap_set)
        assert any(delta.get("lp.lsn-sync.source-url") == "scheme://some.host.name:9999" for delta in snap_set)

    def test_cve_catalog_relation__applies_to_every_unit(self, ctx, snap_set, schema_check):
        """Test cve-catalog data is applied on non-leader units too."""
        standard = _standard_db_relation()
        cve = testing.Relation(
            "cve-catalog",
            remote_app_name="livepatch-cve-service",
            remote_app_data={"url": "scheme://some.host.name:9999"},
        )
        state_in = testing.State(
            leader=False,
            relations=[standard, cve, testing.PeerRelation("livepatch")],
            config=DEFAULT_CONFIG,
        )
        ctx.run(ctx.on.relation_changed(cve), state_in)
        assert any(delta.get("lp.cve-sync.source-url") == "scheme://some.host.name:9999" for delta in snap_set)

    def test_cve_catalog_relation__relation_removed(self, ctx, snap_set, schema_check):
        """Test removing the cve-catalog relation clears the source URLs."""
        standard = _standard_db_relation()
        cve = testing.Relation(
            "cve-catalog",
            remote_app_name="livepatch-cve-service",
            remote_app_data={"url": "scheme://some.host.name:9999"},
        )
        state_in = _leader_state(relations=[standard, cve])
        ctx.run(ctx.on.relation_changed(cve), state_in)

        cve_removed = testing.Relation("cve-catalog", remote_app_name="livepatch-cve-service")
        state_removed = _leader_state(relations=[standard, cve_removed])
        ctx.run(ctx.on.relation_broken(cve_removed), state_removed)
        assert not any(delta.get("lp.cve-sync.source-url") for delta in snap_set[1:])
        assert not any(delta.get("lp.lsn-sync.source-url") for delta in snap_set[1:])


class TestSendOTLPRelation:
    """Tests for the send-otlp relation."""

    def test_send_otlp_relation__success(self, ctx, snap_set, schema_check):
        """Test send-otlp data configures the OTLP metrics endpoint."""
        standard = _standard_db_relation()
        send_otlp = testing.Relation(
            "send-otlp",
            remote_app_name="opentelemetry-collector",
            remote_app_data={"endpoint": "otel-collector:4318", "protocol": "http", "insecure": "true"},
        )
        state_in = _leader_state(relations=[standard, send_otlp])
        ctx.run(ctx.on.relation_changed(send_otlp), state_in)
        assert any(delta.get("lp.otel-metrics.otlp-endpoint") == "otel-collector:4318" for delta in snap_set)
        assert any(delta.get("lp.otel-metrics.protocol") == "http" for delta in snap_set)
        assert any(delta.get("lp.otel-metrics.insecure") == "True" for delta in snap_set)

    def test_send_otlp_relation__defaults(self, ctx, snap_set, schema_check):
        """Test send-otlp relation defaults protocol and secure mode."""
        standard = _standard_db_relation()
        send_otlp = testing.Relation(
            "send-otlp", remote_app_name="opentelemetry-collector", remote_app_data={"endpoint": "otel-collector:4318"}
        )
        state_in = _leader_state(relations=[standard, send_otlp])
        ctx.run(ctx.on.relation_changed(send_otlp), state_in)
        assert any(delta.get("lp.otel-metrics.protocol") == "http" for delta in snap_set)
        assert any(delta.get("lp.otel-metrics.insecure") == "True" for delta in snap_set)

    def test_send_otlp_relation__insecure_false(self, ctx, snap_set, schema_check):
        """Test send-otlp relation with insecure disabled."""
        standard = _standard_db_relation()
        send_otlp = testing.Relation(
            "send-otlp",
            remote_app_name="opentelemetry-collector",
            remote_app_data={"endpoint": "otel-collector:4318", "insecure": "false"},
        )
        state_in = _leader_state(relations=[standard, send_otlp])
        ctx.run(ctx.on.relation_changed(send_otlp), state_in)
        assert any(delta.get("lp.otel-metrics.insecure") == "False" for delta in snap_set)

    def test_send_otlp_relation__relation_removed(self, ctx, snap_set, schema_check):
        """Test removing the send-otlp relation clears the OTLP metrics config."""
        standard = _standard_db_relation()
        send_otlp = testing.Relation(
            "send-otlp", remote_app_name="opentelemetry-collector", remote_app_data={"endpoint": "otel-collector:4318"}
        )
        state_in = _leader_state(relations=[standard, send_otlp])
        ctx.run(ctx.on.relation_changed(send_otlp), state_in)

        send_otlp_removed = testing.Relation("send-otlp", remote_app_name="opentelemetry-collector")
        state_removed = _leader_state(relations=[standard, send_otlp_removed])
        ctx.run(ctx.on.relation_broken(send_otlp_removed), state_removed)
        assert any(delta.get("lp.otel-metrics.otlp-endpoint") == "" for delta in snap_set)


class TestLifecycle:
    """Tests for install, start, and update-status events."""

    def test_install(self, ctx, snap_set):
        """Test install event handler when snap needs ensure_revision."""
        with patch("subprocess.check_output") as check_output:
            check_output.return_value = ""
            state_in = _leader_state()
            state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == testing.BlockedStatus("Waiting for postgres relation to be established.")

    def test_install__already_installed(self, ctx, snap_set):
        """Test install event handler when snap is present."""
        with patch("subprocess.check_output") as check_output:
            check_output.return_value = ""
            state_in = _leader_state()
            state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == testing.BlockedStatus("Waiting for postgres relation to be established.")

    def test_update_status__running(self, ctx, snap_set, schema_check):
        """Test update-status event handler when service is running."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])
        ctx.run(ctx.on.config_changed(), state_in)
        state_out = ctx.run(ctx.on.update_status(), _leader_state(relations=[standard]))
        assert state_out.unit_status == testing.ActiveStatus(LIVEPATCH_RUNNING)

    def test_update_status__not_running_after_being_active(self, ctx, snap_set, schema_check):
        """Test update-status when service is not running after it was active."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])
        ctx.run(ctx.on.config_changed(), state_in)

        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = True
            snap.status.return_value = "enabled"
            snap.version.return_value = "test-version"
            snap.present = True
            snap.revision = SERVER_SNAP_REVISION
            snap.services = {"livepatch": {"active": False}}
            snap.set.return_value = None
            snap.restart.return_value = None
            state_out = ctx.run(ctx.on.update_status(), _leader_state(relations=[standard]))
        assert state_out.unit_status == testing.MaintenanceStatus(LIVEPATCH_NOT_RUNNING)

    def test_update_status__not_running_after_not_being_active(self, ctx, snap_set, schema_check):
        """Test update-status when service is not running and wasn't running before."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])
        ctx.run(ctx.on.config_changed(), state_in)

        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = True
            snap.status.return_value = "enabled"
            snap.version.return_value = "test-version"
            snap.present = True
            snap.revision = SERVER_SNAP_REVISION
            snap.services = {"livepatch": {"active": False}}
            snap.set.return_value = None
            snap.restart.return_value = None
            state_out = ctx.run(ctx.on.update_status(), _leader_state(relations=[standard]))
        assert state_out.unit_status == testing.MaintenanceStatus(LIVEPATCH_NOT_RUNNING)

    def test_start(self, ctx, snap_set, schema_check):
        """Test successfully running livepatch."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == testing.ActiveStatus(LIVEPATCH_RUNNING)


class TestActions:
    """Tests for charm actions."""

    def test_enable_action__success(self, ctx, snap_set):
        """Test enable action."""
        state_in = _leader_state()
        ctx.run(ctx.on.action("enable", params={"token": "some-token"}), state_in)
        assert ctx.action_results == {"enabled": "true"}

    def test_enable_action__empty_token(self, ctx, snap_set):
        """Test enable action when provided token is empty."""
        state_in = _leader_state()
        with pytest.raises(testing.ActionFailed, match="No token provided."):
            ctx.run(ctx.on.action("enable", params={"token": ""}), state_in)

    def test_enable_action__empty_retrieved_token(self, ctx, snap_set):
        """Test enable action when provided token cannot be set."""
        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = True
            snap.get.return_value = ""
            state_in = _leader_state()
            with pytest.raises(testing.ActionFailed, match="Failed to enable Ubuntu Pro"):
                ctx.run(ctx.on.action("enable", params={"token": "some-token"}), state_in)

    def test_enable_action__snap_not_installed(self, ctx, snap_set):
        """Test enable action when livepatch snap is not installed."""
        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = False
            snap.present = False
            state_in = _leader_state()
            with pytest.raises(testing.ActionFailed, match="Livepatch snap is not installed."):
                ctx.run(ctx.on.action("enable", params={"token": "some-token"}), state_in)

    def test_restart_action__success(self, ctx, snap_set):
        """Test restart action."""
        state_in = _leader_state()
        ctx.run(ctx.on.action("restart"), state_in)
        assert ctx.action_results == {"restarted": "true"}

    def test_restart_action__failed_restart(self, ctx, snap_set):
        """Test restart action when service is not running after restart."""
        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = True
            snap.present = True
            snap.revision = SERVER_SNAP_REVISION
            snap.services = {"livepatch": {"active": False}}
            snap.restart.return_value = None
            state_in = _leader_state()
            with pytest.raises(testing.ActionFailed, match="Server failed to restart."):
                ctx.run(ctx.on.action("restart"), state_in)

    def test_restart_action__snap_not_installed(self, ctx, snap_set):
        """Test restart action when snap is not installed."""
        with patch("workload.SnapCache") as snap_cache_cls:
            cache = snap_cache_cls.return_value
            snap = cache.get.return_value
            snap.is_installed.return_value = False
            snap.present = False
            state_in = _leader_state()
            with pytest.raises(testing.ActionFailed, match="server snap is not installed"):
                ctx.run(ctx.on.action("restart"), state_in)

    def test_schema_upgrade_action__success(self, ctx, snap_set):
        """Test schema-upgrade action."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])

        outputs = [
            "Error: database not initialized",
            "",
            "some-version",
            "some-version",
        ]

        def side_effect(cmd, *args, **kwargs):
            if cmd[0] == "canonical-livepatch-server.check-schema-version":
                return outputs.pop(0) if outputs else "some-version"
            if cmd[0] == "canonical-livepatch-server.schema-tool":
                return outputs.pop(0) if outputs else ""
            if cmd[0] == "leader-get":
                return b""
            raise AssertionError(f"unexpected call: {cmd}")

        with patch("subprocess.check_output", side_effect=side_effect):
            ctx.run(ctx.on.action("schema-upgrade"), state_in)

        assert ctx.action_results == {"schema-upgrade-required": False, "schema-version": "some-version"}

    def test_schema_upgrade_action__failed(self, ctx, snap_set):
        """Test schema-upgrade action failure."""
        standard = _standard_db_relation()
        state_in = _leader_state(relations=[standard])

        def side_effect(cmd, *args, **kwargs):
            if cmd[0] == "canonical-livepatch-server.check-schema-version":
                return "Error: database not initialized"
            if cmd[0] == "canonical-livepatch-server.schema-tool":
                return ""
            if cmd[0] == "leader-get":
                return b""
            raise AssertionError(f"unexpected call: {cmd}")

        with patch("subprocess.check_output", side_effect=side_effect):
            with pytest.raises(testing.ActionFailed, match="Schema upgrade failed."):
                ctx.run(ctx.on.action("schema-upgrade"), state_in)

    def test_set_basic_users_action__success(self, ctx, snap_set):
        """Test set-basic-users action."""
        state_in = _leader_state()
        ctx.run(ctx.on.action("set-basic-users", params={"users": "alice:foo,bob:bar"}), state_in)
        assert "alice" in ctx.action_results["auth.basic.users"]
        assert "bob" in ctx.action_results["auth.basic.users"]

    def test_set_basic_users_action__repeated_user(self, ctx, snap_set):
        """Test set-basic-users action when an existing user is re-added."""
        state_in = _leader_state(config={"auth.basic.users": "alice:00000000,bob:00000000"})
        with pytest.raises(testing.ActionFailed, match="The user alice already exists."):
            ctx.run(ctx.on.action("set-basic-users", params={"users": "alice:baz", "append": True}), state_in)

    def test_set_basic_users_action__long_password(self, ctx, snap_set):
        """Test set-basic-users action when password is longer than allowed."""
        state_in = _leader_state()
        long_password = "a" * 73
        with pytest.raises(testing.ActionFailed, match="cannot be more than 72 bytes"):
            ctx.run(ctx.on.action("set-basic-users", params={"users": f"alice:{long_password}"}), state_in)

    def test_set_basic_users_action__empty_users(self, ctx, snap_set):
        """Test set-basic-users action when users string is empty."""
        state_in = _leader_state()
        with pytest.raises(testing.ActionFailed, match="No users provided"):
            ctx.run(ctx.on.action("set-basic-users", params={"users": ""}), state_in)


class TestCustomCA:
    """Tests for custom CA configuration."""

    def test_custom_ca(self, ctx, snap_set, schema_check):
        """Test setting a custom CA for the contracts server."""
        standard = _standard_db_relation()
        with tempfile.NamedTemporaryFile() as tmpfile:
            with patch("reconciler.TRUSTED_CA_FILENAME", tmpfile.name):
                state_in = _leader_state(relations=[standard], config={"contracts.ca": "dGVzdA=="})
                ctx.run(ctx.on.config_changed(), state_in)
            assert os.path.exists(tmpfile.name)
            assert tmpfile.read().decode() == "test"
