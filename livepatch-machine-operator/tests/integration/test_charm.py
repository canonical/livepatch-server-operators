# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Smoke tests for the machine charm."""

from __future__ import annotations

import pathlib

import jubilant
import pytest
from integration.helpers import (
    assert_haproxy_healthy,
    assert_livepatch_healthy,
    deploy_to_active,
)


@pytest.mark.juju_setup
def test_deploy_to_active(juju: jubilant.Juju, charm: pathlib.Path):
    """Deploy the full stack and reach active through the documented guard sequence."""
    status = deploy_to_active(juju, charm)
    assert_livepatch_healthy(status)
    assert_haproxy_healthy(status)
