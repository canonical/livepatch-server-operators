#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for MetricsDB functionality."""

import logging
import pathlib

import jubilant
import pytest
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_NAME, wait_for_app_removed, wait_for_apps

logger = logging.getLogger(__name__)

POSTGRESQL_METRICS_APP = "postgresql-metrics"
POSTGRESQL_METRICS_CHARM = "postgresql"
POSTGRESQL_METRICS_CHANNEL = "16/stable"
POSTGRESQL_METRICS_ENDPOINT = "database"
METRICS_OFFER_NAME = "postgresql-metrics-offer"


@pytest.mark.juju_setup
def test_metrics_db_relation_integration(
    k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: pathlib.Path, app_image_resource: str
):
    """Test MetricsDB relation integration using a machine PostgreSQL offer."""
    deploy_package(k8s_juju, charm=charm, app_image_resource=app_image_resource)

    lxd_juju.deploy(
        POSTGRESQL_METRICS_CHARM,
        POSTGRESQL_METRICS_APP,
        channel=POSTGRESQL_METRICS_CHANNEL,
        base="ubuntu@24.04",
        num_units=1,
    )
    wait_for_apps(lxd_juju, [POSTGRESQL_METRICS_APP], status=ACTIVE_STATUS, timeout=600)

    logger.info("Creating application offer for cross-model relation: %s", METRICS_OFFER_NAME)
    lxd_juju.offer(POSTGRESQL_METRICS_APP, endpoint=POSTGRESQL_METRICS_ENDPOINT, name=METRICS_OFFER_NAME)

    k8s_juju.consume(f"{lxd_juju.model}.{METRICS_OFFER_NAME}")
    k8s_juju.integrate(f"{APP_NAME}:metrics-db", METRICS_OFFER_NAME)

    wait_for_apps(k8s_juju, [APP_NAME], status=ACTIVE_STATUS, timeout=300)

    status = k8s_juju.status()
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS
    assert len(status.apps[APP_NAME].relations["metrics-db"]) > 0


def test_metrics_db_config_options(k8s_juju: jubilant.Juju):
    """Test MetricsDB configuration options."""
    k8s_juju.config(
        APP_NAME,
        {
            "timescale-db.connection-pool-max": "25",
            "timescale-db.connection-lifetime-max": "20m",
            "timescale-db.work-mem": "64",
        },
    )

    wait_for_apps(k8s_juju, [APP_NAME], status=ACTIVE_STATUS, timeout=120)

    status = k8s_juju.status()
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS


def test_metrics_db_relation_broken(k8s_juju: jubilant.Juju):
    """Test behavior when MetricsDB relation is broken."""
    k8s_juju.remove_relation(f"{APP_NAME}:metrics-db", METRICS_OFFER_NAME)

    wait_for_apps(k8s_juju, [APP_NAME], status=ACTIVE_STATUS, timeout=300)

    status = k8s_juju.status()
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS

    relations = status.apps[APP_NAME].relations
    assert "metrics-db" not in relations or len(relations["metrics-db"]) == 0


@pytest.mark.juju_teardown
def test_cleanup(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju):
    """Clean up test artifacts."""
    if POSTGRESQL_METRICS_APP in lxd_juju.status().apps:
        lxd_juju.remove_application(POSTGRESQL_METRICS_APP)
        wait_for_app_removed(lxd_juju, POSTGRESQL_METRICS_APP)

    if APP_NAME in k8s_juju.status().apps:
        k8s_juju.remove_application(APP_NAME)
        wait_for_app_removed(k8s_juju, APP_NAME)
