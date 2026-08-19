# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the pro-airgapped-server relation."""

from __future__ import annotations

import logging
import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    PRO_AIRGAPPED_MANUAL_SERVER_CONFIG,
    PRO_AIRGAPPED_RELATION,
    PRO_AIRGAPPED_SERVER_NAME,
    assert_haproxy_healthy,
    assert_livepatch_healthy,
    deploy_to_active,
    wait_until,
)

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy_to_active(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the full stack and reach active before relating pro-airgapped-server."""
    status = deploy_to_active(juju, charm)
    assert_livepatch_healthy(status)
    assert_haproxy_healthy(status)


def test_integration_with_pro_airgapped_server(juju: jubilant.Juju):
    """Integrating with pro-airgapped-server makes the contracts service report OK."""
    juju.deploy(
        PRO_AIRGAPPED_SERVER_NAME,
        num_units=1,
        config={
            "manual-server-config": PRO_AIRGAPPED_MANUAL_SERVER_CONFIG,
        },
    )
    juju.wait(lambda status: jubilant.all_active(status, PRO_AIRGAPPED_SERVER_NAME), timeout=10 * 60)

    logger.info("Integrating %s and %s", APP_NAME, PRO_AIRGAPPED_SERVER_NAME)
    juju.integrate(f"{APP_NAME}:{PRO_AIRGAPPED_RELATION}", f"{PRO_AIRGAPPED_SERVER_NAME}:livepatch-server")
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME, PRO_AIRGAPPED_SERVER_NAME),
        error=jubilant.any_error,
        timeout=10 * 60,
    )

    def contracts_ok() -> bool:
        status = assert_haproxy_healthy(juju.status())
        return status["contracts"]["status"] == "OK"

    assert wait_until(contracts_ok, timeout=5 * 60, description="the contracts service to report OK")
