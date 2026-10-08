#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the OTLP metrics relation."""

import json
import logging
import pathlib

import jubilant
import pytest
import yaml
from cosl.utils import LZMABase64
from fixtures import deploy_package_if_needed
from helpers import ACTIVE_STATUS, APP_NAME, fast_forward, get_pebble_env, wait_for_app_removed, wait_for_apps

logger = logging.getLogger(__name__)

OTEL_COLLECTOR_APP = "opentelemetry-collector-k8s"
OTEL_COLLECTOR_CHANNEL = "dev/edge"
LIVEPATCH_SEND_OTLP_ENDPOINT = "send-otlp"
COLLECTOR_RECEIVE_OTLP_ENDPOINT = "receive-otlp"


def _get_pebble_env(juju: jubilant.Juju) -> dict:
    """Return the environment dict from the livepatch service's current Pebble plan."""
    return get_pebble_env(juju, unit=f"{APP_NAME}/0")


def _get_published_prometheus_alert_names(juju: jubilant.Juju) -> set:
    """Return the set of Prometheus alert names livepatch published on the send-otlp relation."""
    # The rules are written to livepatch's own app databag, so they are visible as
    # application-data from the collector side (receive-otlp endpoint).
    stdout = juju.cli("show-unit", f"{OTEL_COLLECTOR_APP}/0", "--format=yaml")
    unit_data = yaml.safe_load(stdout)[f"{OTEL_COLLECTOR_APP}/0"]
    app_data: dict = next(
        (
            rel.get("application-data", {})
            for rel in unit_data.get("relation-info", [])
            if rel.get("endpoint") == COLLECTOR_RECEIVE_OTLP_ENDPOINT
        ),
        {},
    )
    assert "rules" in app_data, f"no alert rules published on {COLLECTOR_RECEIVE_OTLP_ENDPOINT}: {app_data}"
    rules = json.loads(LZMABase64.decompress(json.loads(app_data["rules"])))
    groups = rules["promql"].get("groups", [])
    return {rule["alert"] for group in groups for rule in group.get("rules", []) if "alert" in rule}


@pytest.mark.juju_setup
def test_deploy_and_relate_otel_collector(juju: jubilant.Juju, charm: pathlib.Path, app_image_resource: str):
    """Test that livepatch can relate to the OpenTelemetry Collector over the OTLP interface."""
    deploy_package_if_needed(juju, charm=charm, app_image_resource=app_image_resource)

    juju.deploy(OTEL_COLLECTOR_APP, OTEL_COLLECTOR_APP, channel=OTEL_COLLECTOR_CHANNEL, trust=True)

    with fast_forward(juju):
        wait_for_apps(juju, [OTEL_COLLECTOR_APP], status=ACTIVE_STATUS, timeout=600)

    juju.integrate(
        f"{APP_NAME}:{LIVEPATCH_SEND_OTLP_ENDPOINT}",
        f"{OTEL_COLLECTOR_APP}:{COLLECTOR_RECEIVE_OTLP_ENDPOINT}",
    )

    # The collector requires at least one output sink (send-remote-write, send-loki-logs, etc.)
    # to build a valid OTel pipeline.  Deployed standalone here it will go blocked after
    # the receive-otlp relation is created.
    wait_for_apps(juju, [APP_NAME, OTEL_COLLECTOR_APP], timeout=300)

    status = juju.status()
    assert status.apps[APP_NAME].app_status.current == ACTIVE_STATUS


def test_otel_metrics_env_vars_populated(juju: jubilant.Juju):
    """Test that the OTLP endpoint env vars are populated in the Pebble plan after relating."""
    env = _get_pebble_env(juju)

    assert env.get(
        "LP_OTEL_METRICS_OTLP_ENDPOINT"
    ), "LP_OTEL_METRICS_OTLP_ENDPOINT should be set after relating to the collector"
    assert env.get("LP_OTEL_METRICS_PROTOCOL") in (
        "grpc",
        "http",
    ), f"LP_OTEL_METRICS_PROTOCOL should be 'grpc' or 'http', got {env.get('LP_OTEL_METRICS_PROTOCOL')!r}"
    logger.info(
        "OTLP endpoint: %s, protocol: %s, insecure: %s",
        env.get("LP_OTEL_METRICS_OTLP_ENDPOINT"),
        env.get("LP_OTEL_METRICS_PROTOCOL"),
        env.get("LP_OTEL_METRICS_INSECURE"),
    )


def test_prometheus_alert_rules_published(juju: jubilant.Juju):
    """Test that livepatch publishes its Prometheus alert rules over the send-otlp relation."""
    alert_names = _get_published_prometheus_alert_names(juju)
    expected = {
        "LivepatchDatabaseResponseTimeHigh",
        "LivepatchDatabaseErrorsHigh",
        "LivepatchEndpointLatencyHigh",
        "LivepatchContractsServerErrorsHigh",
        "LivepatchContractsTokenErrorsHigh",
    }
    assert expected.issubset(
        alert_names
    ), f"published Prometheus alert rules {alert_names} should include all livepatch alerts {expected}"


def test_otel_metrics_enabled_flag_is_independent(juju: jubilant.Juju):
    """Test that LP_OTEL_METRICS_ENABLED is driven by config, not the relation."""
    env = _get_pebble_env(juju)

    # The relation being present must NOT auto-enable exporting; the operator
    # must explicitly set otel-metrics.enabled=true.
    assert (
        env.get("LP_OTEL_METRICS_ENABLED") == "false"
    ), "LP_OTEL_METRICS_ENABLED should not be set to 'true' by the relation alone"


def test_otel_metrics_enabled_via_config(juju: jubilant.Juju):
    """Test that setting otel-metrics.enabled=true is reflected in the Pebble env."""
    juju.config(APP_NAME, {"otel-metrics.enabled": "true"})

    with fast_forward(juju):
        wait_for_apps(juju, [APP_NAME], status=ACTIVE_STATUS, timeout=120)

    env = _get_pebble_env(juju)
    assert (
        env.get("LP_OTEL_METRICS_ENABLED") == "true"
    ), "LP_OTEL_METRICS_ENABLED should be 'true' after setting otel-metrics.enabled config"

    # Reset for subsequent tests.
    juju.config(APP_NAME, {"otel-metrics.enabled": "false"})
    with fast_forward(juju):
        wait_for_apps(juju, [APP_NAME], status=ACTIVE_STATUS, timeout=120)


def test_otel_metrics_relation_removed_clears_env_vars(juju: jubilant.Juju):
    """Test that removing the OTLP relation clears the endpoint env vars."""
    juju.remove_relation(
        f"{APP_NAME}:{LIVEPATCH_SEND_OTLP_ENDPOINT}",
        f"{OTEL_COLLECTOR_APP}:{COLLECTOR_RECEIVE_OTLP_ENDPOINT}",
    )

    with fast_forward(juju):
        wait_for_apps(juju, [APP_NAME], status=ACTIVE_STATUS, timeout=300)

    env = _get_pebble_env(juju)

    assert not env.get(
        "LP_OTEL_METRICS_OTLP_ENDPOINT"
    ), "LP_OTEL_METRICS_OTLP_ENDPOINT should be cleared after removing the relation"
    assert not env.get(
        "LP_OTEL_METRICS_PROTOCOL"
    ), "LP_OTEL_METRICS_PROTOCOL should be cleared after removing the relation"


@pytest.mark.juju_teardown
def test_cleanup(juju: jubilant.Juju):
    """Remove the OpenTelemetry Collector application."""
    if OTEL_COLLECTOR_APP in juju.status().apps:
        juju.remove_application(OTEL_COLLECTOR_APP)
        wait_for_app_removed(juju, OTEL_COLLECTOR_APP)
