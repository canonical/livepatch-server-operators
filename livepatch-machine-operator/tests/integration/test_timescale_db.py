# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the metrics-db (TimescaleDB) relation."""

from __future__ import annotations

import logging
import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    METRICS_DB_RELATION,
    deploy_core,
    get_snap_config,
    wait_until,
)

logger = logging.getLogger(__name__)

POSTGRESQL_METRICS_APP = "postgresql-metrics"
POSTGRESQL_METRICS_CHARM = "postgresql"
POSTGRESQL_METRICS_ENDPOINT = "database"
# The TimescaleDB extension is only available on PostgreSQL 16, which the
# `postgresql` charm publishes on a 24.04 base.
POSTGRESQL_METRICS_CHANNEL = "16/stable"
POSTGRESQL_METRICS_BASE = "ubuntu@24.04"

METRICS_DB_NAME = "livepatch-metrics-db"
KEY_TIMESCALE_DB_CONNECTION_STRING = "lp.timescale-db.connection-string"


@pytest.mark.juju_setup
def test_metrics_db_relation(juju: jubilant.Juju, charm: pathlib.Path):
    """Relate a dedicated TimescaleDB database and reach active."""
    deploy_core(juju, charm)

    juju.deploy(
        POSTGRESQL_METRICS_CHARM,
        app=POSTGRESQL_METRICS_APP,
        channel=POSTGRESQL_METRICS_CHANNEL,
        base=POSTGRESQL_METRICS_BASE,
        num_units=1,
    )
    # Machine provisioning is slow when many suites share the LXD host.
    juju.wait(
        lambda status: jubilant.all_active(status, POSTGRESQL_METRICS_APP),
        error=jubilant.any_error,
        timeout=30 * 60,
    )

    logger.info("Integrating %s and %s", APP_NAME, POSTGRESQL_METRICS_APP)
    juju.integrate(
        f"{APP_NAME}:{METRICS_DB_RELATION}",
        f"{POSTGRESQL_METRICS_APP}:{POSTGRESQL_METRICS_ENDPOINT}",
    )
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=15 * 60,
    )


def test_timescale_connection_string_is_published(juju: jubilant.Juju):
    """The metrics-db relation populates the timescale connection string snap option."""
    # The charm is already active before the relation is created, so the setup
    # test's `all_active` wait returns before the metrics-db hook has run.
    connection_string = wait_until(
        lambda: get_snap_config(juju).get(KEY_TIMESCALE_DB_CONNECTION_STRING),
        timeout=10 * 60,
        description="the timescale connection string to be published",
    )

    assert connection_string.endswith(f"/{METRICS_DB_NAME}"), connection_string


def test_timescale_config_options_are_applied(juju: jubilant.Juju):
    """TimescaleDB tuning options are passed through to the snap."""
    juju.config(
        APP_NAME,
        {
            "timescale-db.connection-pool-max": "25",
            "timescale-db.connection-lifetime-max": "20m",
            "timescale-db.work-mem": "64",
        },
    )

    expected = {
        "lp.timescale-db.connection-pool-max": "25",
        "lp.timescale-db.connection-lifetime-max": "20m",
        "lp.timescale-db.work-mem": "64",
    }
    wait_until(
        lambda: all(get_snap_config(juju).get(key) == value for key, value in expected.items()),
        timeout=5 * 60,
        description="the timescale config options to be applied",
    )


def test_timescale_connection_string_cleared_on_relation_removed(juju: jubilant.Juju):
    """Removing the metrics-db relation clears the timescale connection string snap option."""
    juju.remove_relation(
        f"{APP_NAME}:{METRICS_DB_RELATION}",
        f"{POSTGRESQL_METRICS_APP}:{POSTGRESQL_METRICS_ENDPOINT}",
    )

    wait_until(
        lambda: not get_snap_config(juju).get(KEY_TIMESCALE_DB_CONNECTION_STRING),
        timeout=10 * 60,
        description="the timescale connection string to be cleared",
    )
