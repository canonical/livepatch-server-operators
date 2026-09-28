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
    LIVEPATCH_FAILED_TO_CONFIGURE,
    LIVEPATCH_FAILED_TO_INSTALL,
    LIVEPATCH_FAILED_TO_RESTART,
    LIVEPATCH_FAILED_TO_UPDATE_CA,
    LIVEPATCH_NOT_RUNNING,
    LIVEPATCH_RUNNING,
    SCHEMA_UPGRADE_FAILED,
)
from constants.snap import SCHEMA_TARGET_TIMESCALE
from livepatch_common.trusted_ca import TRUSTED_CA_FILENAME
from relations import BothDatabaseRelationsError
from util.schema_tool import SchemaToolError, run_schema_upgrade, upgrade_required
from workload import SnapWorkload

logger = logging.getLogger(__name__)


class MachineReconciler:
    """Drives the machine towards the charm's goal state."""

    def __init__(self, charm: Any, workload: SnapWorkload) -> None:
        """Construct.

        Args:
            charm: the charm being reconciled.
            workload: the Livepatch snap workload.
        """
        self._charm = charm
        self._workload = workload
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
            schema_upgraded = self._converge_schema(db_uri, self._charm.metrics_db_uri)
        except (OSError, SchemaToolError) as e:
            logger.error("failed to upgrade the database schema: %s", e)
            self.status = BlockedStatus(SCHEMA_UPGRADE_FAILED)
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
            timescale_db_uri=charm.metrics_db_uri,
            cve_catalog_url=relations.cve_catalog_url(charm),
            otel_metrics_endpoint=relations.otel_metrics_endpoint(charm),
        )
        try:
            config_changed = self._workload.apply_config(desired)
        except SnapError as e:
            logger.error("failed to apply snap configuration: %s", e)
            self.status = MaintenanceStatus(LIVEPATCH_FAILED_TO_CONFIGURE)
            return

        # -- Converge the daemon.
        running = self._workload.running
        restart_reasons = {
            "snap installed or refreshed": snap_changed,
            "trusted CA certificate updated": ca_changed,
            "snap configuration changed": config_changed,
            "database schema upgraded": schema_upgraded,
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

    # -- Schema helpers.

    def _converge_schema(self, db_uri: str, metrics_db_uri: Optional[str] = None) -> bool:
        """Migrate the database schemas, if they are not already up to date.

        Every unit runs this on every reconcile; the snap serializes concurrent
        upgrade attempts internally, so no peer coordination is needed here.

        Args:
            db_uri: the primary database connection string to check and migrate.
            metrics_db_uri: the metrics/Timescale database connection string, if
                the metrics-db relation is established.

        Returns:
            True if a migration was performed this reconcile.

        Raises:
            SchemaToolError: if the check or upgrade command failed.
        """
        upgraded = False
        if upgrade_required(db_uri):
            logger.info("database schema requires a migration; running the upgrade")
            run_schema_upgrade(db_uri)
            upgraded = True
        if metrics_db_uri and upgrade_required(metrics_db_uri, target=SCHEMA_TARGET_TIMESCALE):
            logger.info("metrics database schema requires a migration; running the upgrade")
            run_schema_upgrade(metrics_db_uri, target=SCHEMA_TARGET_TIMESCALE)
            upgraded = True
        return upgraded

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
        current_ca = path.read_text() if path.exists() else None

        if config.has_invalid_contracts_ca:
            return False
        if desired is None:
            if current_ca is None:
                logger.debug("ca config not set")
                return False
            path.unlink()
        else:
            if current_ca == desired:
                return False
            path.write_text(desired)

        try:
            result = subprocess.check_output(
                ["update-ca-certificates", "--fresh"], text=True, stderr=subprocess.STDOUT
            )  # nosec
        except (OSError, subprocess.CalledProcessError):
            # Restore the source file so the next reconcile retries the refresh.
            if current_ca is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(current_ca)
            raise
        logger.info("output update-ca-certificates: %s", result)
        return True
