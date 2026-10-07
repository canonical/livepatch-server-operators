#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib

import jubilant
import pytest
from fixtures import deploy_package_if_needed
from helpers import ACTIVE_STATUS, APP_NAME, wait_for_apps

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the charm and its dependencies."""
    deploy_package_if_needed(juju, charm=charm)


def test_charm_integrates(juju: jubilant.Juju):
    """Test charm can integrate both ingress interfaces."""
    # nginx
    juju.config(APP_NAME, {"ingress-interface": "legacy-nginx-route"})
    juju.deploy("nginx-ingress-integrator", channel="latest/stable", trust=True)
    juju.integrate(f"{APP_NAME}:nginx-route", "nginx-ingress-integrator:nginx-route")
    status = wait_for_apps(juju, [APP_NAME, "nginx-ingress-integrator"], status=ACTIVE_STATUS, timeout=600)

    assert status.apps["nginx-ingress-integrator"].app_status.current == ACTIVE_STATUS

    # ingress via gateway API
    juju.config(APP_NAME, {"ingress-interface": "ingress"})

    juju.deploy("self-signed-certificates", channel="1/stable")
    juju.deploy("gateway-api-integrator", channel="latest/stable", trust=True)
    juju.deploy("gateway-route-configurator", channel="latest/stable")

    external_hostname = "livepatch.com"
    juju.config("gateway-api-integrator", {"gateway-class": "traefik", "external-hostname": external_hostname})
    juju.config("gateway-route-configurator", {"hostname": external_hostname})

    juju.integrate("self-signed-certificates:certificates", "gateway-api-integrator:certificates")
    juju.integrate("gateway-api-integrator:gateway-route", "gateway-route-configurator:gateway-route")

    juju.integrate(f"{APP_NAME}:ingress", "gateway-route-configurator:ingress")

    # successes=30 at 1s intervals approximates libjuju's idle_period=30.
    status = wait_for_apps(
        juju,
        [APP_NAME, "gateway-route-configurator", "gateway-api-integrator", "self-signed-certificates"],
        status=ACTIVE_STATUS,
        timeout=1200,
        successes=30,
    )

    assert status.apps["gateway-api-integrator"].app_status.current == ACTIVE_STATUS
    assert status.apps["gateway-route-configurator"].app_status.current == ACTIVE_STATUS
    assert status.apps["self-signed-certificates"].app_status.current == ACTIVE_STATUS
