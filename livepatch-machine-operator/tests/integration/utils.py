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
CHARM_BASE_ENV_VAR = "CHARM_BASE"


def _local_charms() -> list[Path]:
    """Return the `.charm` files present in the charm root, narrowed to CHARM_BASE when it is set."""
    base = os.environ.get(CHARM_BASE_ENV_VAR)
    pattern = f"*{base}-*.charm" if base else "*.charm"
    return sorted(path.resolve() for path in Path().glob(pattern))


def build_charm() -> Path:
    """Pack the charm with `charmcraft` and return the resulting path."""
    LOGGER.info("Packing charm with charmcraft")
    command = ["charmcraft", "pack", "--project-dir", ".", "--verbosity=brief"]
    base = os.environ.get(CHARM_BASE_ENV_VAR)
    if base:
        command += ["--platform", f"{base}:amd64"]
    subprocess.run(command, check=True)  # nosec B603, B607

    charms = _local_charms()
    assert charms, f"No charms were found in {Path().absolute()} after packing"
    assert len(charms) == 1, f"Found more than one charm {charms}"
    return charms[0]


def _resolve_charm_path(charm_path: str) -> Path:
    """Resolve CHARM_PATH, which opcli renders relative to the monorepo root.

    Tox runs from the charm directory, so a relative path is tried against the
    current directory and then each ancestor.
    """
    path = Path(charm_path)
    if path.is_absolute():
        return path.resolve()

    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        candidate = (base / path).resolve()
        if candidate.is_file():
            return candidate
    return (cwd / path).resolve()


def resolve_charm() -> Path:
    """Resolve the charm archive used by the integration tests.

    Resolution order matches the documented fixture pattern, with one local
    development enhancement: if no packed charm is present yet, build one with
    `charmcraft pack`.
    """
    charm = os.environ.get(CHARM_PATH_ENV_VAR)
    if charm:
        path = _resolve_charm_path(charm)
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
