# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Schema upgrade action."""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from ops.charm import ActionEvent
from ops.model import MaintenanceStatus

from util.schema_tool import check_schema_version, run_schema_upgrade

if TYPE_CHECKING:
    from charm import OperatorMachineCharm


def _check_schema_upgrade_ran(self: OperatorMachineCharm) -> Tuple[bool, str]:
    """Check if a schema upgrade has run.

    Args:
        self: the charm the action is running on.

    Returns:
        A tuple of whether an upgrade is required, and the reported version.
    """
    db_uri = self.database_uri
    if not self.unit.is_leader() or not db_uri:
        # The unit is not a leader, so it shouldn't worry about performing a schema upgrade.
        return False, ""
    return check_schema_version(db_uri)


def on_schema_upgrade_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Run schema upgrade action."""
    event.log("Running schema upgrade action...")
    if self.unit.is_leader():
        # TODO: the livepatch server snap does not yet expose a command that only
        # *checks* whether a schema upgrade is required without also running it.
        # Once such a command is available, use it here to skip invoking
        # `run_schema_upgrade` altogether when no upgrade is actually needed,
        # rather than running it unconditionally and comparing versions
        # before/after as we do below.
        _, version_before = _check_schema_upgrade_ran(self)

        run_schema_upgrade(self.database_uri)
        upgrade_required, version_after = _check_schema_upgrade_ran(self)

        if upgrade_required:
            failure_reason = "Schema upgrade failed."
            self.unit.status = MaintenanceStatus(failure_reason)
            event.log(failure_reason)
            event.set_results(
                {
                    "schema-upgrade-required": upgrade_required,
                    "schema-version": version_after,
                }
            )
            event.fail(failure_reason)
        else:
            event.log("Schema upgrade was successful.")
            event.set_results(
                {
                    "schema-upgrade-required": upgrade_required,
                    "schema-version": version_after,
                }
            )

            if version_after != version_before:
                self._restart_all_units()
            else:
                event.log("Schema was already up to date, skipping restart.")
