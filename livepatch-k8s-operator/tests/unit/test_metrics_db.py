# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Livepatch k8s charm's metrics-db relation."""

import dataclasses

from ops import testing

from .conftest import APP_CONTAINER, METRICS_DSN, SERVICE_NAME, metrics_db_relation


def test_metrics_db_sets_connection_string_env_var(context, base_state):
    """arrange: leader unit with a metrics-db relation providing valid credentials.
    act: metrics-db database-created (relation-changed with full credentials).
    assert: LP_TIMESCALE_DB_CONNECTION_STRING is set to the metrics DSN.
    """
    relation = metrics_db_relation()
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_TIMESCALE_DB_CONNECTION_STRING"] == METRICS_DSN
    assert state_out.unit_status == testing.ActiveStatus()


def test_metrics_db_absent_without_relation(context, base_state):
    """arrange: leader unit, no metrics-db relation.
    act: config-changed.
    assert: LP_TIMESCALE_DB_CONNECTION_STRING is absent.
    """
    state_out = context.run(context.on.config_changed(), testing.State(**base_state))

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert "LP_TIMESCALE_DB_CONNECTION_STRING" not in env


def test_metrics_db_incomplete_credentials_yield_no_connection_string(context, base_state):
    """arrange: a metrics-db relation missing the password field.
    act: metrics-db relation-changed.
    assert: LP_TIMESCALE_DB_CONNECTION_STRING is absent (incomplete data is ignored).
    """
    relation = metrics_db_relation(password="")
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_changed(relation), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert "LP_TIMESCALE_DB_CONNECTION_STRING" not in env


def test_timescale_db_config_options_are_mapped_generically(context, base_state):
    """arrange: leader unit with timescale_db.* config set and the relation present.
    act: config-changed.
    assert: the config options are mapped to LP_TIMESCALE_DB_* the same way any
        other charm config is (LivepatchApp.gen_environment() doesn't special-case them).
    """
    relation = metrics_db_relation()
    state_in = dataclasses.replace(
        testing.State(**base_state),
        relations=set(base_state["relations"]) | {relation},
        config={
            "timescale_db.enabled": True,
            "timescale_db.connection_pool_max": 20,
            "timescale_db.connection_lifetime_max": "30m",
            "timescale_db.work_mem": 32,
        },
    )

    state_out = context.run(context.on.config_changed(), state_in)

    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert env["LP_TIMESCALE_DB_ENABLED"] == "true"
    assert env["LP_TIMESCALE_DB_CONNECTION_POOL_MAX"] == "20"
    assert env["LP_TIMESCALE_DB_CONNECTION_LIFETIME_MAX"] == "30m"
    assert env["LP_TIMESCALE_DB_WORK_MEM"] == "32"


def test_metrics_db_broken_restarts_without_metrics_connection_string(context, base_state):
    """arrange: leader unit with an established metrics-db relation about to be removed.
    act: metrics-db relation-broken.
    assert: the charm restarts and no longer advertises the metrics DB connection string.
    """
    relation = metrics_db_relation()
    state_in = dataclasses.replace(testing.State(**base_state), relations=set(base_state["relations"]) | {relation})

    state_out = context.run(context.on.relation_broken(relation), state_in)

    assert state_out.unit_status == testing.ActiveStatus()
    env = state_out.get_container(APP_CONTAINER).plan.services[SERVICE_NAME].environment
    assert "LP_TIMESCALE_DB_CONNECTION_STRING" not in env
