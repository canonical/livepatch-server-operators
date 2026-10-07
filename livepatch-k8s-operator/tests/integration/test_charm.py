#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib

import jubilant
import pytest
import requests
from fixtures import deploy_package
from helpers import ACTIVE_STATUS, APP_IMAGE, APP_NAME, APP_PORT, extract_version_from_image

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the charm and its dependencies."""
    deploy_package(juju, charm=charm)


def test_application_is_up(juju: jubilant.Juju):
    """Test the app is up and running."""
    logger.info("Getting model status")
    status = juju.status()
    logger.info(f"Status: {status}")
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS

    app_address = status.apps[APP_NAME].units[f"{APP_NAME}/0"].address
    url = f"http://{app_address}:{APP_PORT}/debug/status"
    logger.info("Querying app address: %s", url)
    r = requests.get(url, timeout=2.0)
    assert r.status_code == 200
    logger.info(f"Output = {r.json()}")


def test_charm_version_is_set(juju: jubilant.Juju):
    """Test correct version is set"""
    expected_version = extract_version_from_image(APP_IMAGE)
    if expected_version is None:
        pytest.skip(f"app image {APP_IMAGE!r} carries no vX.Y.Z tag to compare against")

    version = juju.status().apps[APP_NAME].charm_version
    assert version == expected_version, f"expected {expected_version}, got {version}"
