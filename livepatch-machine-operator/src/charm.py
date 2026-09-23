#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Livepatch machine operator charm.

This charm follows the reconciler (holistic) pattern: every Juju event of
interest runs the same converge path, which re-reads all of its inputs and
drives the machine towards the goal state.

Only events that cannot be reconciled keep a dedicated handler: actions, the
teardown event, and the relation events whose payload is not otherwise
readable.
"""

import logging
from typing import Optional

import pgsql
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.operator_libs_linux.v2.snap import Snap
from ops import main
from ops.charm import CharmBase, CollectStatusEvent

import relations
from config import OPT_SERVER_ADDRESS, parse_server_port
from livepatch_common.relations import DATABASE_NAME, strip_query_parameters
from log_redactor import setup_log_redaction
from reconciler import MachineReconciler
from relations import (
    DATABASE_RELATION,
    DATABASE_RELATION_LEGACY,
    METRICS_DB_NAME,
    METRICS_DB_RELATION,
    PEER_RELATION,
)
from state import State
from workload import SnapWorkload

logger = logging.getLogger(__name__)


class OperatorMachineCharm(CharmBase):
    """Livepatch on-premise machine charm."""

    @property
    def get_livepatch_snap(self) -> Snap:
        """Retrieves livepatch snap from the snap cache.

        Returns:
            The Livepatch server snap.
        """
        return self.workload.snap

    @property
    def livepatch_installed(self) -> bool:
        """Reports if the Livepatch snap is installed.

        Returns:
            True if the snap is present on the machine.
        """
        return self.workload.present

    @property
    def livepatch_running(self) -> bool:
        """Reports if the "livepatch" snap daemon is running.

        Returns:
            True if the daemon is active.
        """
        return self.workload.running

    @property
    def database_uri(self) -> Optional[str]:
        """The PostgreSQL connection string, from whichever relation is in use.

        Returns:
            The connection string, or None while no database is usable.
        """
        return relations.database_uri(self.database) or self._legacy_database_uri

    @property
    def metrics_db_uri(self) -> Optional[str]:
        """The timescale/metrics database connection string, if related.

        Returns:
            The connection string from the metrics-db relation, or None.
        """
        return relations.metrics_db_uri(self.metrics_db)

    @property
    def _legacy_database_uri(self) -> Optional[str]:
        """The connection string captured from the legacy database relation.

        Returns:
            The connection string, or None if the relation is not in use.
        """
        if not relations.is_legacy_database_relation_activated(self):
            return None
        if not self._unit_state.is_ready():
            return None
        uri = self._unit_state.legacy_db_uri
        if uri:
            return uri
        # Charm revisions before this one relayed the legacy URI through
        # application data. Fall back to it so that units upgrading from those
        # revisions stay up until their next master-changed event.
        if self._state.is_ready():
            return self._state.db_uri
        return None

    def __init__(self, *args) -> None:
        """Init function."""
        super().__init__(*args)
        setup_log_redaction()

        # Peer relation backed state. The application-scoped store holds values
        # shared by every unit; the unit-scoped store holds per-unit values.
        self._state = State(self.app, lambda: self.model.get_relation(PEER_RELATION))
        self._unit_state = State(self.unit, lambda: self.model.get_relation(PEER_RELATION))

        self.workload = SnapWorkload()
        self.reconciler = MachineReconciler(self, self.workload)

        # Database (legacy).
        self.db = pgsql.PostgreSQLClient(self, DATABASE_RELATION_LEGACY)

        # Database.
        self.database = DatabaseRequires(
            self,
            relation_name=DATABASE_RELATION,
            database_name=DATABASE_NAME,
        )

        # Second, optional PostgreSQL/TimescaleDB database for time-series
        # metrics.
        self.metrics_db = DatabaseRequires(
            self,
            relation_name=METRICS_DB_RELATION,
            database_name=METRICS_DB_NAME,
        )

        # Observability.
        server_port = parse_server_port(self.config.get(OPT_SERVER_ADDRESS))
        if server_port is None:
            logger.warning("Server address config missing port.")
            logger.warning("Won't enable COS observability.")
        else:
            self._grafana_agent = COSAgentProvider(
                self,
                relation_name="cos-agent",
                metrics_endpoints=[
                    {"path": "/metrics", "port": server_port},
                ],
                metrics_rules_dir="./src/alert_rules/prometheus",
                logs_rules_dir="./src/alert_rules/loki",
                recurse_rules_dirs=True,
                dashboard_dirs=["./src/grafana_dashboards"],
            )

        # Every event of interest runs the same converge path. The reconciler
        # re-reads all of its inputs, so the event itself carries no meaning.
        #
        # `secret_changed` is included because the database library delivers
        # credentials as Juju secrets but does not re-emit its own events when
        # one rotates, so this is the only signal that they have changed.
        for event in (
            self.on.install,
            self.on.start,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.update_status,
            self.on.secret_changed,
            self.on.livepatch_relation_changed,
            self.on.website_relation_joined,
            self.on.website_relation_changed,
            self.on.database_relation_broken,
            self.database.on.database_created,
            self.database.on.endpoints_changed,
            self.metrics_db.on.database_created,
            self.metrics_db.on.endpoints_changed,
            self.on.metrics_db_relation_broken,
            self.on.cve_catalog_relation_changed,
            self.on.cve_catalog_relation_broken,
            self.on.send_otlp_relation_changed,
            self.on.send_otlp_relation_broken,
            self.on.pro_airgapped_server_relation_changed,
            self.on.pro_airgapped_server_relation_departed,
            self.on.pro_airgapped_server_relation_broken,
            self.db.on.standby_changed,
        ):
            self.framework.observe(event, self._reconcile)

        # The only relation events needing their own handler are the two the
        # converge path cannot serve: one requests a database by writing to the
        # event object, and the other carries data that cannot be read back.
        self.framework.observe(self.db.on.database_relation_joined, self._on_legacy_db_relation_joined)
        self.framework.observe(self.db.on.master_changed, self._on_legacy_db_master_changed)

        # Teardown.
        self.framework.observe(self.on.stop, self._on_stop)

        # Actions.
        self.framework.observe(self.on.enable_action, self.on_enable_action)
        self.framework.observe(self.on.schema_upgrade_action, self.on_schema_upgrade_action)
        self.framework.observe(self.on.set_basic_users_action, self.on_set_basic_users_action)
        self.framework.observe(self.on.restart_action, self.on_restart_action)

        # Status is reported once, at the end of every hook.
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

    ##############
    # RECONCILER #
    ##############

    def _reconcile(self, event) -> None:
        """Converge the machine towards the goal state.

        Args:
            event: the triggering event, which is deliberately ignored.
        """
        self.reconciler.reconcile(event)

    def _on_collect_unit_status(self, event: CollectStatusEvent) -> None:
        """Report the status computed by the reconciler.

        No status is added when the reconciler did not run, which leaves any
        status set by an action handler in place.

        Args:
            event: the collect-status event.
        """
        if self.reconciler.status is not None:
            event.add_status(self.reconciler.status)

    ############
    # TEARDOWN #
    ############

    def _on_stop(self, event) -> None:
        """Stop the workload as the unit is torn down.

        Args:
            event: the stop event.
        """
        self.workload.stop()

    ###################
    # LEGACY DATABASE #
    ###################

    def _on_legacy_db_relation_joined(self, event: pgsql.DatabaseRelationJoinedEvent) -> None:
        """Claim the database name on the legacy database relation.

        This is the one relation event that cannot be reconciled: a database is
        requested by writing to the event object, which is only possible while
        the event is being handled.

        Args:
            event: the legacy database relation-joined event.
        """
        logging.info("(postgresql, legacy database relation) RELATION_JOINED event fired.")

        logging.warning(
            f"`{DATABASE_RELATION_LEGACY}` is a legacy relation; try integrating with `{DATABASE_RELATION}` instead."
        )

        if not self.model.unit.is_leader():
            return

        if relations.is_database_relation_activated(self):
            # Claiming a database here would provision a second one. The
            # relation is left dormant and the converge path reports the
            # conflict as a blocked status.
            logging.error(f"The `{DATABASE_RELATION}` relation is already integrated; not claiming a database.")
            return

        event.database = DATABASE_NAME

    def _on_legacy_db_master_changed(self, event: pgsql.MasterChangedEvent) -> None:
        """Capture this unit's primary connection string, then reconcile.

        The primary is only available on the event object, so it is recorded
        for the converge path to read. Every unit receives this event, so each
        one records its own copy in unit-scoped data rather than depending on
        the leader to relay it.

        Args:
            event: the legacy database master-changed event.
        """
        logging.info("(postgresql, legacy database relation) MASTER_CHANGED event fired.")

        if event.database != DATABASE_NAME:
            logging.debug("(legacy database relation) Database setup not complete yet, returning.")
            return

        # wokeignore:rule=master
        if event.master is not None:
            # Note (babakks): The split is mainly to drop query parameters that may cause further database
            # connection errors. For example, there's this query parameters, named `fallback_application_name`,
            # which causes the schema upgrade command to return `unrecognized configuration parameter
            # "fallback_application_name" (SQLSTATE 42704)`.
            # wokeignore:rule=master
            self._unit_state.legacy_db_uri = strip_query_parameters(event.master.uri)
        else:
            self._unit_state.legacy_db_uri = None

        self._reconcile(event)

    ###########
    # ACTIONS #
    ###########
    from actions.enable import on_enable_action
    from actions.restart import on_restart_action
    from actions.schema_upgrade import on_schema_upgrade_action
    from actions.set_basic_users import on_set_basic_users_action

    ###########
    # UTILITY #
    ###########

    def set_status_and_log(self, msg, status) -> None:
        """Log and set unit status simultaneously.

        This is only used by action handlers, which report failures
        synchronously. The converge path reports status through
        `collect_unit_status` instead.

        Args:
            msg: the message to log and report.
            status: the status class to instantiate with the message.
        """
        logging.info(msg)
        self.unit.status = status(msg)


if __name__ == "__main__":
    main(OperatorMachineCharm)
