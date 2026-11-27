# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State-transition (Scenario) tests for the Livepatch k8s charm's secret-storage initialization."""

import dataclasses

from ops import testing

from .conftest import SECRET_STORAGE_KEY


def _with_secret_storage_data(base_state: dict, local_app_data: dict, leader: bool = True) -> testing.State:
    peer = testing.PeerRelation("secret-storage", local_app_data=local_app_data)
    others = {r for r in base_state["relations"] if r.endpoint != "secret-storage"}
    return dataclasses.replace(testing.State(**base_state), leader=leader, relations=others | {peer})


def test_config_changed_initializes_missing_secret_key_on_leader(context, base_state):
    """arrange: leader whose secret-storage peer relation exists but has no key, as after
        refreshing from the pre-12-factor charm (relation-created never reached this code).
    act: config-changed.
    assert: the key is written and the charm proceeds to active in the same hook.
    """
    state_in = _with_secret_storage_data(base_state, local_app_data={})

    state_out = context.run(context.on.config_changed(), state_in)

    peer = state_out.get_relations("secret-storage")[0]
    assert peer.local_app_data.get(SECRET_STORAGE_KEY)
    assert state_out.unit_status == testing.ActiveStatus()


def test_config_changed_preserves_existing_secret_key(context, base_state):
    """arrange: leader with a secret key already stored.
    act: config-changed.
    assert: the stored key is left untouched.
    """
    state_in = _with_secret_storage_data(base_state, local_app_data={SECRET_STORAGE_KEY: "existing-key"})

    state_out = context.run(context.on.config_changed(), state_in)

    peer = state_out.get_relations("secret-storage")[0]
    assert peer.local_app_data[SECRET_STORAGE_KEY] == "existing-key"


def test_config_changed_on_non_leader_does_not_initialize_secret_key(context, base_state):
    """arrange: non-leader with an empty secret-storage app databag.
    act: config-changed.
    assert: nothing is written and the unit waits for the leader to initialize storage.
    """
    state_in = _with_secret_storage_data(base_state, local_app_data={}, leader=False)

    state_out = context.run(context.on.config_changed(), state_in)

    peer = state_out.get_relations("secret-storage")[0]
    assert SECRET_STORAGE_KEY not in peer.local_app_data
    assert state_out.unit_status == testing.WaitingStatus("Waiting for peer integration")


def test_leader_elected_initializes_missing_secret_key(context, base_state, mock_leader_settings):
    """arrange: unit just elected leader while the secret-storage app databag is empty.
    act: leader-elected.
    assert: the key is written.
    """
    state_in = _with_secret_storage_data(base_state, local_app_data={})

    state_out = context.run(context.on.leader_elected(), state_in)

    peer = state_out.get_relations("secret-storage")[0]
    assert peer.local_app_data.get(SECRET_STORAGE_KEY)
