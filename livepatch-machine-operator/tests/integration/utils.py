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
DEFAULT_CHARM_BASE = "ubuntu@22.04"


def _charm_base() -> str:
    """Return the base of the charm under test, matching `helpers.CHARM_BASE`."""
    return os.environ.get(CHARM_BASE_ENV_VAR) or DEFAULT_CHARM_BASE


def _local_charms() -> list[Path]:
    """Return the `.charm` files present in the charm root, for the base under test."""
    pattern = f"*{_charm_base()}-*.charm"
    return sorted(path.resolve() for path in Path().glob(pattern))


def build_charm() -> Path:
    """Pack the charm with `charmcraft` and return the resulting path."""
    LOGGER.info("Packing charm with charmcraft")
    command = ["charmcraft", "pack", "--project-dir", ".", "--verbosity=brief"]
    command += ["--platform", f"{_charm_base()}:amd64"]
    env = {
        **os.environ,
        # ubuntu@26.04 is still experimental for the `charm` craft-parts plugin.
        "CHARMCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS": "true",
    }
    subprocess.run(command, check=True, env=env)  # nosec B603, B607

    charms = _local_charms()
    if not charms:
        raise FileNotFoundError(f"No charms were found in {Path().absolute()} after packing")
    if len(charms) != 1:
        raise RuntimeError(f"Found more than one charm {charms}")
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
        if not path.is_file():
            raise FileNotFoundError(f"{path} is not a file")
        LOGGER.info("Using charm file from %s: %s", CHARM_PATH_ENV_VAR, path)
        return path

    charms = _local_charms()
    if not charms:
        return build_charm()

    if len(charms) != 1:
        raise RuntimeError(f"Found more than one charm {charms}")
    path = charms[0]
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")
    LOGGER.info("Using charm file discovered in the project root: %s", path)
    return path
