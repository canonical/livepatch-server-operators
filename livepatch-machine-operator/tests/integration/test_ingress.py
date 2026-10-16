# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for ingress to the machine charm through HAProxy.

The machine charm has no gateway/ingress relation: its only ingress is the
`website` (http) relation it provides, fronted by the `haproxy` charm.
"""

from __future__ import annotations

import logging
import pathlib

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    HAPROXY_NAME,
    STACK_BASE,
    assert_haproxy_healthy,
    assert_livepatch_healthy,
    deploy_core,
    integrate_haproxy,
    website_relation_port,
)

logger = logging.getLogger(__name__)

# `server.server-address` defaults to `0.0.0.0:8080`.
DEFAULT_SERVER_PORT = "8080"


@pytest.mark.juju_setup
def test_deploy_with_haproxy(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the charm and HAProxy, reaching active with the website relation up."""
    deploy_core(juju, charm)

    juju.deploy(HAPROXY_NAME, num_units=1, base=STACK_BASE)
    juju.wait(
        lambda status: jubilant.all_active(status, HAPROXY_NAME),
        error=jubilant.any_error,
        timeout=20 * 60,
    )

    integrate_haproxy(juju)
    status = juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME, HAPROXY_NAME),
        error=jubilant.any_error,
        timeout=10 * 60,
    )
    assert_livepatch_healthy(status)


def test_website_relation_publishes_server_port(juju: jubilant.Juju):
    """The charm publishes the Livepatch server port on the website relation."""
    assert website_relation_port(juju) == DEFAULT_SERVER_PORT


def test_haproxy_proxies_livepatch(juju: jubilant.Juju):
    """A request through HAProxy reaches the Livepatch server (HTTP 200)."""
    assert_haproxy_healthy(juju.status())
