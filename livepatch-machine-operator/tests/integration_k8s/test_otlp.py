#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cross-model integration tests for the OTLP metrics relation.

The machine charm is related to an OpenTelemetry Collector running on
Kubernetes. This mirrors production deployments, where a machine-deployed
Livepatch Server pushes metrics to a Collector that lives in the observability
(K8s) cluster.
"""

import logging
import pathlib
from typing import Optional

import jubilant
import pytest
from integration.helpers import (
    APP_NAME,
    SEND_OTLP_RELATION,
    deploy_core,
    get_snap_config,
    wait_until,
)

logger = logging.getLogger(__name__)

OTEL_COLLECTOR_APP = "opentelemetry-collector-k8s"
OTEL_COLLECTOR_CHANNEL = "dev/edge"
COLLECTOR_RECEIVE_OTLP_ENDPOINT = "receive-otlp"
OTLP_OFFER_NAME = "otlp-collector-offer"

KEY_OTLP_ENDPOINT = "lp.otel-metrics.otlp-endpoint"
KEY_OTLP_PROTOCOL = "lp.otel-metrics.protocol"
KEY_OTLP_INSECURE = "lp.otel-metrics.insecure"
KEY_OTLP_ENABLED = "lp.otel-metrics.enabled"


def _endpoint_config(juju: jubilant.Juju) -> Optional[dict]:
    """Return the snap config once the OTLP endpoint is populated, else None."""
    config = get_snap_config(juju)
    return config if config.get(KEY_OTLP_ENDPOINT) else None


@pytest.mark.juju_setup
def test_deploy_and_relate_otel_collector(juju: jubilant.Juju, k8s_juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the machine charm and relate its OTLP endpoint to a K8s Collector."""
    deploy_core(juju, charm)

    logger.info("Deploying %s on Kubernetes", OTEL_COLLECTOR_APP)
    k8s_juju.deploy(OTEL_COLLECTOR_APP, OTEL_COLLECTOR_APP, channel=OTEL_COLLECTOR_CHANNEL, trust=True)
    k8s_juju.wait(
        lambda status: jubilant.all_active(status, OTEL_COLLECTOR_APP),
        error=jubilant.any_error,
        timeout=30 * 60,
    )

    logger.info("Offering %s as %s", COLLECTOR_RECEIVE_OTLP_ENDPOINT, OTLP_OFFER_NAME)
    k8s_juju.offer(OTEL_COLLECTOR_APP, endpoint=COLLECTOR_RECEIVE_OTLP_ENDPOINT, name=OTLP_OFFER_NAME)
    juju.consume(f"{k8s_juju.model}.{OTLP_OFFER_NAME}")
    juju.integrate(f"{APP_NAME}:{SEND_OTLP_RELATION}", OTLP_OFFER_NAME)

    # The Collector needs an output sink to build a valid OTel pipeline, so it
    # goes blocked once the receive-otlp relation exists. The Livepatch unit
    # must still settle active.
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=15 * 60,
    )


def test_otel_metrics_endpoint_configured(juju: jubilant.Juju):
    """Test that the OTLP endpoint from the relation is written to the snap config."""
    config = wait_until(
        lambda: _endpoint_config(juju),
        timeout=300,
        description=f"{KEY_OTLP_ENDPOINT} to be populated",
    )

    assert config.get(KEY_OTLP_PROTOCOL) in (
        "grpc",
        "http",
    ), f"{KEY_OTLP_PROTOCOL} should be 'grpc' or 'http', got {config.get(KEY_OTLP_PROTOCOL)!r}"
    logger.info(
        "OTLP endpoint: %s, protocol: %s, insecure: %s",
        config.get(KEY_OTLP_ENDPOINT),
        config.get(KEY_OTLP_PROTOCOL),
        config.get(KEY_OTLP_INSECURE),
    )


def test_otel_metrics_enabled_flag_is_independent(juju: jubilant.Juju):
    """Test that the relation does not auto-enable metrics export."""
    config = get_snap_config(juju)

    # Presence of the relation must NOT turn exporting on; the operator has to
    # set otel-metrics.enabled=true explicitly.
    assert (
        config.get(KEY_OTLP_ENABLED) == "false"
    ), f"{KEY_OTLP_ENABLED} should be 'false' after relating alone, got {config.get(KEY_OTLP_ENABLED)!r}"


def test_otel_metrics_enabled_via_config(juju: jubilant.Juju):
    """Test that otel-metrics.enabled=true is reflected in the snap config."""
    juju.config(APP_NAME, {"otel-metrics.enabled": "true"})
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=5 * 60,
    )

    assert get_snap_config(juju).get(KEY_OTLP_ENABLED) == "true"

    juju.config(APP_NAME, {"otel-metrics.enabled": "false"})
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=5 * 60,
    )


def test_otel_metrics_relation_removed_clears_keys(juju: jubilant.Juju):
    """Test that removing the OTLP relation clears the endpoint snap keys."""
    juju.remove_relation(f"{APP_NAME}:{SEND_OTLP_RELATION}", OTLP_OFFER_NAME)
    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        error=jubilant.any_error,
        timeout=10 * 60,
    )

    wait_until(
        lambda: not get_snap_config(juju).get(KEY_OTLP_ENDPOINT),
        timeout=300,
        description=f"{KEY_OTLP_ENDPOINT} to be cleared",
    )
    config = get_snap_config(juju)
    assert not config.get(KEY_OTLP_ENDPOINT)
    assert not config.get(KEY_OTLP_PROTOCOL)


@pytest.mark.juju_teardown
def test_cleanup(k8s_juju: jubilant.Juju):
    """Remove the OpenTelemetry Collector application."""
    if OTEL_COLLECTOR_APP in k8s_juju.status().apps:
        k8s_juju.remove_application(OTEL_COLLECTOR_APP)
