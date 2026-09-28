# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for the Livepatch k8s charm's state-transition (Scenario) tests."""

import subprocess

import pytest
import yaml
from ops import testing

from charm import LivepatchCharm

# The go-framework extension always names the workload container "app" and the
# Pebble service after the framework name ("go"), regardless of the charm's own name.
APP_CONTAINER = "app"
SERVICE_NAME = "go"
APP_NAME = "canonical-livepatch-server-k8s"

# Key used by paas_charm's KeySecretStorage to persist the app secret key in the
# "secret-storage" peer relation's application databag (framework name is "go").
SECRET_STORAGE_KEY = "go_secret_key"

# Minimal Pebble base plan simulating what the go-framework rock bakes in: a "go"
# service definition that paas_charm's App.restart() rewrites in place.
DEFAULT_LAYER = {
    "services": {
        SERVICE_NAME: {
            "override": "replace",
            "command": f"/usr/local/bin/{APP_NAME}",
            "startup": "enabled",
            "user": "_daemon_",
        }
    }
}


def postgresql_relation(**remote_app_data) -> testing.Relation:
    """Build a `postgresql` relation with valid PaaSDatabaseRequires data by default."""
    data = {
        "endpoints": "postgresql-primary.test-model.svc.cluster.local:5432",
        "username": "livepatch",
        "password": "test-password",  # nosec B105
        "database": "canonical-livepatch-server-k8s",
    }
    data.update(remote_app_data)
    return testing.Relation(endpoint="postgresql", interface="postgresql_client", remote_app_data=data)


# The connection string that `postgresql_relation()`'s defaults resolve to; this is
# what `LP_DATABASE_CONNECTION_STRING`/`POSTGRESQL_DB_CONNECT_STRING` will contain.
MAIN_DSN = (
    "postgresql://livepatch:test-password"
    "@postgresql-primary.test-model.svc.cluster.local:5432/canonical-livepatch-server-k8s"
)


def metrics_db_relation(**remote_app_data) -> testing.Relation:
    """Build a `metrics-db` relation with valid PaaSDatabaseRequires data by default."""
    data = {
        "endpoints": "timescale-primary.test-model.svc.cluster.local:5432",
        "username": "livepatch-metrics",
        "password": "metrics-password",  # nosec B105
        "database": "livepatch-metrics-db",
    }
    data.update(remote_app_data)
    return testing.Relation(endpoint="metrics-db", interface="postgresql_client", remote_app_data=data)


# The connection string that `metrics_db_relation()`'s defaults resolve to.
# PaaSDatabaseRequires.to_relation_data() uses the *relation name* as the URI scheme
# (not "postgresql"), so this is "metrics-db://...", not "postgresql://...".
METRICS_DSN = (
    "metrics-db://livepatch-metrics:metrics-password"
    "@timescale-primary.test-model.svc.cluster.local:5432/livepatch-metrics-db"
)

SCHEMA_TOOL_PATH = "/usr/local/bin/livepatch-schema-tool"


@pytest.fixture
def context() -> testing.Context:
    """A Context rooted at the Livepatch charm."""
    return testing.Context(LivepatchCharm)


@pytest.fixture
def base_state() -> dict:
    """A leader unit with its secret-storage peer relation, database and container ready.

    This is the state needed for `PaasCharm.is_ready()` to return True, which most
    custom relation/action handlers rely on (via `restart()`) before they do anything.
    """
    return {
        "leader": True,
        "relations": {
            testing.PeerRelation("secret-storage", local_app_data={SECRET_STORAGE_KEY: "test-secret-key"}),
            postgresql_relation(),
        },
        "containers": {testing.Container(APP_CONTAINER, can_connect=True, _base_plan=DEFAULT_LAYER)},
        "model": testing.Model(name="test-model"),
    }


@pytest.fixture
def mock_leader_settings(monkeypatch):
    """Stub the `leader-get`/`leader-set` hook tools used directly by `ops-lib-pgsql`.

    That library predates ops's relation/secret testing hooks and shells out to the
    `leader-get`/`leader-set` Juju hook tools directly, which Scenario doesn't mock.
    """
    store: dict = {}
    # Capture the real implementations before patching, so the fallback branches
    # below don't recurse into the patched functions themselves.
    real_check_output = subprocess.check_output
    real_check_call = subprocess.check_call

    def fake_check_output(cmd, *args, **kwargs):
        if cmd[0] == "leader-get":
            return yaml.safe_dump(store.get(cmd[-1])).encode("utf-8")
        return real_check_output(cmd, *args, **kwargs)  # pragma: no cover

    def fake_check_call(cmd, *args, **kwargs):
        if cmd[0] == "leader-set":
            for pair in cmd[1:]:
                key, _, value = pair.partition("=")
                store[key] = value
            return 0
        return real_check_call(cmd, *args, **kwargs)  # pragma: no cover

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(subprocess, "check_call", fake_check_call)
    return store


@pytest.fixture
def schema_tool_container(tmp_path) -> testing.Container:
    """A container like the default one, but with `livepatch-schema-tool` present."""
    schema_tool = tmp_path / "livepatch-schema-tool"
    schema_tool.write_text("#!/bin/sh\n")
    return testing.Container(
        APP_CONTAINER,
        can_connect=True,
        _base_plan=DEFAULT_LAYER,
        mounts={"schema-tool": testing.Mount(location=SCHEMA_TOOL_PATH, source=schema_tool)},
    )
