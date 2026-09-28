# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Schema upgrade action."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ops.charm import ActionEvent
from ops.model import MaintenanceStatus

from constants.snap import SCHEMA_TARGET_TIMESCALE
from util.schema_tool import SchemaToolError, check_schema_version, run_schema_upgrade, upgrade_required

if TYPE_CHECKING:
    from charm import OperatorMachineCharm


def on_schema_upgrade_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Manually trigger a schema upgrade.

    The reconciler already does this automatically, on every unit, on every
    reconcile; this action exists for manual/diagnostic use, and restarts the
    daemon immediately rather than waiting for the next hook.
    """
    event.log("Running schema upgrade action...")
    db_uri = self.database_uri
    if not db_uri:
        failure_reason = "Database is not ready, cannot run schema upgrade."
        event.log(failure_reason)
        event.fail(failure_reason)
        return

    metrics_db_uri = self.metrics_db_uri
    try:
        if upgrade_required(db_uri):
            run_schema_upgrade(db_uri)
        if metrics_db_uri and upgrade_required(metrics_db_uri, target=SCHEMA_TARGET_TIMESCALE):
            run_schema_upgrade(metrics_db_uri, target=SCHEMA_TARGET_TIMESCALE)
        schema_upgrade_required = upgrade_required(db_uri)
        if metrics_db_uri:
            schema_upgrade_required = schema_upgrade_required or upgrade_required(
                metrics_db_uri, target=SCHEMA_TARGET_TIMESCALE
            )
        _, version = check_schema_version(db_uri)
    except (OSError, SchemaToolError) as e:
        failure_reason = f"Failed to run the schema upgrade command: {e}"
        self.unit.status = MaintenanceStatus(failure_reason)
        event.log(failure_reason)
        event.fail(failure_reason)
        return

    event.set_results(
        {
            "schema-upgrade-required": schema_upgrade_required,
            "schema-version": version,
        }
    )
    event.log("Schema upgrade was successful.")

    # Converges CA/config drift and restarts the daemon if anything changed.
    self._reconcile(None)

