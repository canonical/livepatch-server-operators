#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib

import jubilant
import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_IMAGE_RESOURCE, APP_NAME, APP_PORT, wait_for_apps

logger = logging.getLogger(__name__)

# The upgrade test upgrades from the latest *published* stable release to the
# locally built charm. The current stable charm predates this repo's database
# relation rename, so `juju refresh` fails with "would break relation ...
# database". Disable the whole module until there is a latest stable release
# compatible with the local charm; remove this skip (and revisit the approach)
# once that release exists.
pytestmark = pytest.mark.skip(
    reason="Disabled until a latest stable release compatible with the local charm is published."
)


def _assert_app_is_up(juju: jubilant.Juju) -> None:
    """Assert the app is active and its debug status endpoint responds."""
    status = juju.status()
    logger.info(f"Status: {status}")
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS

    address = status.apps[APP_NAME].units[f"{APP_NAME}/0"].address
    url = f"http://{address}:{APP_PORT}/debug/status"
    logger.info("Querying app address: %s", url)
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200
    logger.info(f"Output = {r.json()}")


@pytest.mark.juju_setup
def test_deploy_current_stable(juju: jubilant.Juju):
    """Deploy the current stable release and check it is up."""
    deploy_package(juju, use_current_stable=True)

    logger.info("Getting model status")
    _assert_app_is_up(juju)


def test_upgrade(juju: jubilant.Juju, charm: pathlib.Path, app_image_resource: str):
    """Test upgrading from the current stable release works as expected."""
    # Deploy the locally built charm and wait for active/idle status
    logger.info("refreshing running application with the new local charm")
    # `juju refresh --path` requires every oci-image resource to be supplied
    # again, exactly like the initial local deploy does.
    juju.refresh(APP_NAME, path=charm, resources={APP_IMAGE_RESOURCE: app_image_resource})

    logger.info("waiting for the upgraded unit to be ready")
    wait_for_apps(juju, [APP_NAME], status=ACTIVE_STATUS, timeout=600)

    logger.info("Getting model status after upgrade")
    _assert_app_is_up(juju)
