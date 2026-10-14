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
)

logger = logging.getLogger(__name__)

POSTGRESQL_METRICS_APP = "postgresql-metrics"
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
        POSTGRESQL_METRICS_APP,
        channel=POSTGRESQL_METRICS_CHANNEL,
        base=POSTGRESQL_METRICS_BASE,
        num_units=1,
        config={"plugin-timescaledb-enable": True},
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
    config = get_snap_config(juju)

    connection_string = config.get(KEY_TIMESCALE_DB_CONNECTION_STRING, "")
    assert connection_string, "timescale connection string should be set once the metrics-db relation exists"
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

    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=5 * 60,
    )

    config = get_snap_config(juju)
    assert config.get("lp.timescale-db.connection-pool-max") == "25"
    assert config.get("lp.timescale-db.connection-lifetime-max") == "20m"
    assert config.get("lp.timescale-db.work-mem") == "64"


def test_timescale_connection_string_cleared_on_relation_removed(juju: jubilant.Juju):
    """Removing the metrics-db relation clears the timescale connection string snap option."""
    juju.remove_relation(
        f"{APP_NAME}:{METRICS_DB_RELATION}",
        f"{POSTGRESQL_METRICS_APP}:{POSTGRESQL_METRICS_ENDPOINT}",
    )

    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=10 * 60,
    )

    config = get_snap_config(juju)
    assert not config.get(KEY_TIMESCALE_DB_CONNECTION_STRING), (
        "timescale connection string should be cleared after removing the relation"
    )
