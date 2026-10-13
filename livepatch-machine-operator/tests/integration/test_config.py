# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the reconciler's configuration and relation guards."""

from __future__ import annotations

import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    AWAIT_POSTGRES_RELATION,
    CHARM_BASE,
    DATABASE_RELATION,
    DATABASE_RELATION_LEGACY,
    POSTGRES_NAME,
    STACK_BASE,
    unit_workload_message,
)

VALID_URL_TEMPLATE = "http://livepatch:8080/v1/patches/{filename}"


@pytest.mark.juju_setup
def test_deploy_for_config_guards(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the charm and PostgreSQL without integrating them."""
    juju.deploy(charm, app=APP_NAME, num_units=1, base=CHARM_BASE)
    juju.deploy(POSTGRES_NAME, channel="14/stable", num_units=1, base=STACK_BASE)
    juju.wait(lambda status: jubilant.all_active(status, POSTGRES_NAME), timeout=15 * 60)
    juju.wait(lambda status: jubilant.all_blocked(status, APP_NAME), timeout=10 * 60)


def test_blocked_on_missing_url_template(juju: jubilant.Juju):
    """With no configuration set, the charm blocks asking for server.url-template."""
    message = unit_workload_message(juju.status(), APP_NAME)
    assert "server.url-template" in message
    assert "not set" in message


def test_blocked_on_invalid_server_address(juju: jubilant.Juju):
    """An invalid server.server-address blocks the charm with a clear message."""
    juju.config(
        APP_NAME,
        {
            "server.url-template": VALID_URL_TEMPLATE,
            "server.server-address": "not-a-valid-address",
        },
    )
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and "server.server-address" in unit_workload_message(status, APP_NAME),
        timeout=5 * 60,
    )
    assert "host:port" in unit_workload_message(juju.status(), APP_NAME)

    juju.config(APP_NAME, {"server.server-address": "0.0.0.0:8080"})
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and unit_workload_message(status, APP_NAME) == AWAIT_POSTGRES_RELATION,
        timeout=5 * 60,
    )


@pytest.mark.juju_teardown
def test_blocked_when_both_database_relations_active(juju: jubilant.Juju):
    """Integrating both database relations at once blocks the charm."""
    juju.integrate(f"{APP_NAME}:{DATABASE_RELATION}", f"{POSTGRES_NAME}:{DATABASE_RELATION}")
    juju.integrate(f"{APP_NAME}:{DATABASE_RELATION_LEGACY}", f"{POSTGRES_NAME}:db")

    juju.wait(
        lambda status: (
            jubilant.all_blocked(status, APP_NAME) and "not allowed" in unit_workload_message(status, APP_NAME)
        ),
        timeout=10 * 60,
    )
    assert "database" in unit_workload_message(juju.status(), APP_NAME)

    juju.remove_relation(f"{APP_NAME}:{DATABASE_RELATION_LEGACY}", f"{POSTGRES_NAME}:db")
    juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=10 * 60)
