# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Schema tool action."""

import subprocess  # nosec
from typing import Tuple

from constants.errors import SCHEMA_VERSION_CHECK_ERROR
from constants.snap import (
    SCHEMA_UPGRADE_COMMAND,
    SCHEMA_VERSION_CHECK,
    SERVER_SNAP_NAME,
)


class SchemaToolError(RuntimeError):
    """Raised when a schema-tool command fails unexpectedly."""


def run_schema_version_check(master_uri: str) -> str:
    """Run a schema version check against the database."""
    _cmd = [f"{SERVER_SNAP_NAME}.{SCHEMA_VERSION_CHECK}", master_uri]
    result = "failed"
    try:
        result = subprocess.check_output(_cmd, text=True, stderr=subprocess.STDOUT)  # nosec

    except subprocess.CalledProcessError as e:
        result = e.output or ""
        lines = result.splitlines()
        if not lines or SCHEMA_VERSION_CHECK_ERROR not in lines[0]:
            raise SchemaToolError("schema version check command failed") from e
    return result


def check_schema_version(db_uri: str) -> Tuple[bool, str]:
    """Check whether the database schema needs upgrading.

    Args:
        db_uri: the database connection string to check.

    Returns:
        A tuple of whether an upgrade is required, and the reported version.
    """
    result = run_schema_version_check(master_uri=db_uri)
    lines = result.splitlines()
    if lines and lines[0].find(SCHEMA_VERSION_CHECK_ERROR) != -1:
        return True, result
    return False, result


def run_schema_upgrade(master_uri: str) -> str:
    """Run a schema upgrade against the desired Postgres URI."""
    _cmd = [
        f"{SERVER_SNAP_NAME}.{SCHEMA_UPGRADE_COMMAND}",
        master_uri,
    ]
    result = "failed"
    try:
        result = subprocess.check_output(_cmd, text=True, stderr=subprocess.STDOUT)  # nosec

    except subprocess.CalledProcessError as e:
        raise SchemaToolError("schema upgrade command failed") from e
    return result
