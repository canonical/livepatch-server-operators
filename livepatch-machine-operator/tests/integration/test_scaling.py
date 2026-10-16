# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for scaling and redeploying the machine charm."""

from __future__ import annotations

import logging
import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    AWAIT_POSTGRES_RELATION,
    CHARM_BASE,
    assert_haproxy_healthy,
    assert_livepatch_healthy,
    deploy_to_active,
    integrate_haproxy,
    integrate_postgres,
    scale,
    unit_workload_message,
)

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy_to_active(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the full stack and reach active before scaling."""
    status = deploy_to_active(juju, charm)
    assert_livepatch_healthy(status)
    assert_haproxy_healthy(status)


def test_scale_up_and_down(juju: jubilant.Juju):
    """Scaling the application up and down leaves every unit healthy."""
    status = scale(juju, APP_NAME, 2)
    assert_livepatch_healthy(status, unit=0)
    assert_livepatch_healthy(status, unit=1)
    assert_haproxy_healthy(status)

    status = scale(juju, APP_NAME, 1)
    assert_livepatch_healthy(status, unit=0)
    assert_haproxy_healthy(status)


@pytest.mark.juju_setup
@pytest.mark.juju_teardown
def test_charm_redeploy(juju: jubilant.Juju, charm: pathlib.Path):
    """The charm can be destroyed and redeployed against the same database."""
    assert_livepatch_healthy(juju.status())

    logger.info("Destroying %s", APP_NAME)
    juju.remove_application(APP_NAME, destroy_storage=True, force=True)
    juju.wait(lambda status: APP_NAME not in status.apps, error=jubilant.any_error, timeout=10 * 60)

    logger.info("Redeploying %s", APP_NAME)
    juju.deploy(
        charm,
        app=APP_NAME,
        num_units=1,
        base=CHARM_BASE,
        config={
            "patch-storage.type": "postgres",
            "server.url-template": "http://livepatch:8080/v1/patches/{filename}",
        },
    )

    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and unit_workload_message(status, APP_NAME) == AWAIT_POSTGRES_RELATION,
        error=jubilant.any_error,
        timeout=10 * 60,
    )

    integrate_postgres(juju)
    integrate_haproxy(juju)

    status = juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=10 * 60)
    assert_livepatch_healthy(status)
    assert_haproxy_healthy(status)
