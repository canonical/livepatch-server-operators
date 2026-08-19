# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utilities for resolving the charm file under test."""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404
from pathlib import Path

LOGGER = logging.getLogger(__name__)

CHARM_PATH_ENV_VAR = "CHARM_PATH"


def _local_charms() -> list[Path]:
    """Return the `.charm` files present in the charm root."""
    return sorted(path.resolve() for path in Path().glob("*.charm"))


def build_charm() -> Path:
    """Pack the charm with `charmcraft` and return the resulting path."""
    LOGGER.info("Packing charm with charmcraft")
    subprocess.run(["charmcraft", "pack", "--project-dir", ".", "--verbosity=brief"], check=True)  # nosec B603, B607

    charms = _local_charms()
    assert charms, f"No charms were found in {Path().absolute()} after packing"
    assert len(charms) == 1, f"Found more than one charm {charms}"
    return charms[0]


def resolve_charm() -> Path:
    """Resolve the charm archive used by the integration tests.

    Resolution order matches the documented fixture pattern, with one local
    development enhancement: if no packed charm is present yet, build one with
    `charmcraft pack`.
    """
    charm = os.environ.get(CHARM_PATH_ENV_VAR)
    if charm:
        path = Path(charm).resolve()
        assert path.is_file(), f"{path} is not a file"
        LOGGER.info("Using charm file from %s: %s", CHARM_PATH_ENV_VAR, path)
        return path

    charms = _local_charms()
    if not charms:
        return build_charm()

    assert len(charms) == 1, f"Found more than one charm {charms}"
    path = charms[0]
    assert path.is_file(), f"{path} is not a file"
    LOGGER.info("Using charm file discovered in the project root: %s", path)
    return path
