# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Schema tool action."""

import subprocess  # nosec
from typing import Optional, Tuple

from constants.errors import SCHEMA_VERSION_CHECK_ERROR
from constants.snap import (
    CHECK_SCHEMA_UPGRADE_COMMAND,
    SCHEMA_UPGRADE_COMMAND,
    SCHEMA_VERSION_CHECK,
    SERVER_SNAP_NAME,
)

# Exit codes of `check-schema-upgrade`.
_SCHEMA_UP_TO_DATE = 0
_SCHEMA_UPGRADE_REQUIRED = 2


class SchemaToolError(RuntimeError):
    """Raised when a schema-tool command fails unexpectedly."""


def upgrade_required(db_uri: str, target: Optional[str] = None) -> bool:
    """Check whether the database schema needs upgrading.

    Args:
        db_uri: the database connection string to check.
        target: the schema target to check (e.g. ``livepatchdb`` or
            ``timescale``). When ``None`` the tool's default target is used,
            which corresponds to the primary ``livepatchdb`` schema. The
            metrics/Timescale database must be checked with the Timescale-
            specific target so its own migrations are evaluated.

    Returns:
        True if a schema upgrade is required.

    Raises:
        SchemaToolError: if the command could not be run or exited with an
            unexpected (non 0/2) code.
    """
    _cmd = [f"{SERVER_SNAP_NAME}.{CHECK_SCHEMA_UPGRADE_COMMAND}", db_uri]
    if target:
        _cmd.extend(["--target", target])
    try:
        result = subprocess.run(_cmd, text=True, capture_output=True)  # nosec
    except OSError as e:
        raise SchemaToolError("check-schema-upgrade command failed to run") from e

    if result.returncode == _SCHEMA_UP_TO_DATE:
        return False
    if result.returncode == _SCHEMA_UPGRADE_REQUIRED:
        return True
    raise SchemaToolError(
        f"check-schema-upgrade exited with unexpected code {result.returncode}: {result.stdout}{result.stderr}"
    )


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


def run_schema_upgrade(master_uri: str, target: Optional[str] = None) -> str:
    """Run a schema upgrade against the desired Postgres URI.

    Args:
        master_uri: the database connection string to migrate.
        target: the schema target to migrate. ``None`` uses the tool's
            default (primary) target; the metrics/Timescale database must
            be migrated with the Timescale-specific target.
    """
    _cmd = [
        f"{SERVER_SNAP_NAME}.{SCHEMA_UPGRADE_COMMAND}",
        master_uri,
    ]
    if target:
        _cmd.extend(["--target", target])
    result = "failed"
    try:
        result = subprocess.check_output(_cmd, text=True, stderr=subprocess.STDOUT)  # nosec

    except subprocess.CalledProcessError as e:
        raise SchemaToolError("schema upgrade command failed") from e
    return result
