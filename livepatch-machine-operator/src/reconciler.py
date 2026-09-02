# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""The single converge path for the Livepatch machine charm.

Every Juju event of interest runs :meth:`MachineReconciler.reconcile`, which
drives the machine towards the goal state: a pinned, held snap, configured from
the charm configuration and the database relation, with a running daemon.

The reconciler is idempotent. Each component it drives reports whether it
actually changed anything, and the daemon is only restarted when something did.
Unit status is never set here; the reconciler records the status it computed on
:attr:`MachineReconciler.status`, and the charm reports it from
``collect_unit_status``.
"""

from __future__ import annotations

import logging
import subprocess  # nosec
from pathlib import Path
from typing import Any, Optional

from charms.operator_libs_linux.v2.snap import SnapError
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    StatusBase,
    WaitingStatus,
)

import relations
from config import CharmConfig, InvalidCharmConfigError
from constants.statuses import (
    AWAIT_POSTGRES_PRIMARY,
    AWAIT_POSTGRES_RELATION,
    AWAIT_POSTGRES_URI,
    LIVEPATCH_FAILED_TO_CHECK_SCHEMA,
    LIVEPATCH_FAILED_TO_CONFIGURE,
    LIVEPATCH_FAILED_TO_INSTALL,
    LIVEPATCH_FAILED_TO_RESTART,
    LIVEPATCH_FAILED_TO_UPDATE_CA,
    LIVEPATCH_NOT_RUNNING,
    LIVEPATCH_RUNNING,
    SCHEMA_UPGRADE_REQUIRED,
)
from relations import BothDatabaseRelationsError
from util.schema_tool import check_schema_version
from workload import SnapWorkload

logger = logging.getLogger(__name__)

TRUSTED_CA_FILENAME = "/usr/local/share/ca-certificates/trusted-contracts.ca.crt"


class MachineReconciler:
    """Drives the machine towards the charm's goal state."""

    def __init__(self, charm: Any, workload: SnapWorkload, app_state: Any, unit_state: Any) -> None:
        """Construct.

        Args:
            charm: the charm being reconciled.
            workload: the Livepatch snap workload.
            app_state: application-scoped peer relation state.
            unit_state: unit-scoped peer relation state.
        """
        self._charm = charm
        self._workload = workload
        self._state = app_state
        self._unit_state = unit_state
        self.status: Optional[StatusBase] = None

    def reconcile(self, _: Any = None) -> None:
        """Converge the machine towards the goal state.

        The event that triggered reconciliation is deliberately ignored: all
        state is re-read from the machine, the configuration and the relations.

        Args:
            _: the triggering event, unused.
        """
        self._reconcile()

    def _reconcile(self) -> None:
        """Run the converge path."""
        charm = self._charm

        # -- Converge the snap itself.
        # This runs ahead of every guard so the workload is installed, pinned
        # and held even while the charm is blocked on configuration or on a
        # database relation.
        try:
            snap_changed = self._workload.ensure_revision()
        except SnapError as e:
            logger.error("failed to install or refresh the snap: %s", e)
            self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_INSTALL)
            return

        # -- Gather and guard: configuration.
        try:
            config = CharmConfig.from_charm(charm)
        except InvalidCharmConfigError as e:
            logger.warning("invalid charm configuration: %s", e)
            self.status = BlockedStatus(str(e))
            return

        # Outgoing relation data only depends on configuration, so it is
        # republished before the remaining prerequisites are checked.
        relations.push_outgoing(charm, config.server_port)

        # -- Gather and guard: database relations.
        try:
            relations.check_database_relations(charm)
        except BothDatabaseRelationsError as e:
            logger.error("%s", e)
            self.status = BlockedStatus(str(e))
            return

        db_uri = self._database_uri()
        if not db_uri:
            self.status = self._database_pending_status()
            return

        try:
            schema_migrated = self._schema_migrated(db_uri)
        except OSError as e:
            logger.error("failed to check the database schema version: %s", e)
            self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_CHECK_SCHEMA)
            return
        if not schema_migrated:
            self.status = BlockedStatus(SCHEMA_UPGRADE_REQUIRED)
            return

        # -- Converge trusted certificates and snap configuration.
        try:
            ca_changed = self._converge_trusted_ca(config)
        except (OSError, subprocess.CalledProcessError) as e:
            logger.error("failed to update the trusted CA certificate: %s", e)
            self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_UPDATE_CA)
            return
        desired = config.to_snap_config(
            db_uri=db_uri,
            is_leader=charm.unit.is_leader(),
            model_uuid=charm.model.uuid,
            pro_airgapped_url=relations.pro_airgapped_url(charm),
        )
        try:
            config_changed = self._workload.apply_config(desired)
        except SnapError as e:
            logger.error("failed to apply snap configuration: %s", e)
            self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_CONFIGURE)
            return
        restart_cue = self._pending_restart_cue()

        # -- Converge the daemon.
        running = self._workload.running
        restart_reasons = {
            "snap installed or refreshed": snap_changed,
            "trusted CA certificate updated": ca_changed,
            "snap configuration changed": config_changed,
            "restart cue pending": restart_cue is not None,
            "daemon not running": not running,
        }
        if any(restart_reasons.values()):
            met_reasons = [reason for reason, met in restart_reasons.items() if met]
            logger.info("restarting the livepatch daemon (%s)", "; ".join(met_reasons))
            try:
                self._workload.restart()
            except SnapError as e:
                logger.error("failed to restart the snap: %s", e)
                self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_RESTART)
                return
            running = self._workload.running
            if restart_cue is not None:
                # Only recorded once the restart succeeded, so a failure is
                # retried on the next hook.
                self._unit_state.applied_restart_cue = restart_cue

        self.status = ActiveStatus(LIVEPATCH_RUNNING) if running else MaintenanceStatus(LIVEPATCH_NOT_RUNNING)

    # -- Gathering helpers.

    def _database_uri(self) -> Optional[str]:
        """Read the resolved database connection string.

        Returns:
            The connection string, or None if it is not available yet.
        """
        return self._charm.database_uri

    def _database_pending_status(self) -> StatusBase:
        """Describe why the database connection string is unavailable.

        Returns:
            The status to report while the database is not usable.
        """
        charm = self._charm
        database = relations.is_database_relation_activated(charm)
        legacy = relations.is_legacy_database_relation_activated(charm)
        if not database and not legacy:
            return BlockedStatus(AWAIT_POSTGRES_RELATION)
        if database:
            return WaitingStatus(AWAIT_POSTGRES_URI)
        return WaitingStatus(AWAIT_POSTGRES_PRIMARY)

    def _pending_restart_cue(self) -> Optional[int]:
        """Report an outstanding restart request from the leader.

        Actions that change the workload without changing its configuration
        bump a counter in application data. Each unit records the value it has
        acted on in its own unit data, so the request is applied exactly once
        per unit.

        Returns:
            The cue value to act on, or None if there is nothing outstanding.
        """
        if not self._state.is_ready() or not self._unit_state.is_ready():
            return None
        cue = self._state.restart_cue or 0
        if cue == (self._unit_state.applied_restart_cue or 0):
            return None
        return cue

    # -- Schema helpers.

    def _schema_migrated(self, db_uri: str) -> bool:
        """Check that the database schema has been migrated.

        Args:
            db_uri: the database connection string to check.

        Returns:
            True if the schema is up to date.
        """
        upgrade_required, version = check_schema_version(db_uri)
        if upgrade_required:
            logger.warning("database schema requires a migration")
            return False

        logger.info("database has been migrated; current version: %s", version)
        return True

    # -- Certificate helpers.

    def _converge_trusted_ca(self, config: CharmConfig) -> bool:
        """Install or remove the configured contracts CA certificate, if it changed.

        Rewriting the trust store and refreshing it is expensive, so it only
        happens when the configured certificate differs from the installed one.

        Args:
            config: the validated charm configuration.

        Returns:
            True if the trust store was updated.
        """
        desired = config.contracts_ca
        path = Path(TRUSTED_CA_FILENAME)

        if desired is None:
            if not path.exists():
                logger.debug("ca config not set")
                return False
            path.unlink()
        else:
            if path.exists() and path.read_text() == desired:
                return False
            path.write_text(desired)

        result = subprocess.check_output(
            ["update-ca-certificates", "--fresh"], text=True, stderr=subprocess.STDOUT
        )  # nosec
        logger.info("output update-ca-certificates: %s", result)
        return True
