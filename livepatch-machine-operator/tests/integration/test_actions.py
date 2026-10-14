# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for machine charm actions."""

from __future__ import annotations

import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    assert_livepatch_healthy,
    deploy_to_active,
    run_schema_upgrade,
)


@pytest.mark.juju_setup
def test_deploy_to_active(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the full stack and reach active before exercising actions."""
    status = deploy_to_active(juju, charm)
    assert_livepatch_healthy(status)


def test_schema_upgrade_action_is_idempotent(juju: jubilant.Juju):
    """Running the schema-upgrade action again reports that no upgrade is needed."""
    task = run_schema_upgrade(juju)
    assert task.results["schema-upgrade-required"] == "False"

    status = juju.status()
    assert_livepatch_healthy(status)


def test_restart_action(juju: jubilant.Juju):
    """The restart action restarts the daemon and leaves the unit active."""
    task = juju.run(f"{APP_NAME}/0", "restart")
    assert task.results["restarted"] == "true"

    status = juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=5 * 60)
    assert_livepatch_healthy(status)


def test_set_basic_users_action(juju: jubilant.Juju):
    """The set-basic-users action hashes passwords without changing server config."""
    task = juju.run(
        f"{APP_NAME}/0",
        "set-basic-users",
        {"users": "alice:hunter2,bob:correcthorsebatterystaple"},
    )

    hashed = task.results.get("auth.basic.users") or task.results.get("auth-basic-users")
    assert hashed is not None, f"set-basic-users returned unexpected results: {task.results}"
    users = dict(pair.split(":", 1) for pair in hashed.split(","))
    assert set(users) == {"alice", "bob"}
    for password_hash in users.values():
        assert password_hash.startswith("$2b$")

    status = juju.status()
    assert jubilant.all_active(status, APP_NAME)
    assert_livepatch_healthy(status)
