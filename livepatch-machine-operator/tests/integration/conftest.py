# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test fixtures for the machine charm."""

from __future__ import annotations

import pathlib

import pytest
from integration.utils import resolve_charm


@pytest.fixture(scope="session")
def charm() -> pathlib.Path:
    """Return the path of the charm under test."""
    return resolve_charm()
