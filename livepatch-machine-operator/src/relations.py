# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relation data gathering and publishing for the Livepatch machine charm.

All relation I/O lives here: resolving the PostgreSQL connection string from
either the database or the legacy database relation, reading the air-gapped Pro
contracts address, and re-publishing this unit's outgoing databags.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from ops.model import ModelError, Relation

from livepatch_common.relations import build_database_uri, cve_source_url
from livepatch_common.relations import otlp_metrics_endpoint as otlp_endpoint_from_data
from livepatch_common.relations import (
    pro_airgapped_server_address as airgapped_address_from_data,
)

logger = logging.getLogger(__name__)

DATABASE_RELATION = "database"
DATABASE_RELATION_LEGACY = "database-legacy"
METRICS_DB_NAME = "livepatch-metrics-db"
METRICS_DB_RELATION = "metrics-db"
CVE_CATALOG_RELATION = "cve-catalog"
SEND_OTLP_RELATION = "send-otlp"
PRO_AIRGAPPED_SERVER_RELATION = "pro-airgapped-server"
WEBSITE_RELATION = "website"
PEER_RELATION = "livepatch"


class BothDatabaseRelationsError(Exception):
    """Raised when both the database and legacy database relations are active."""


def is_database_relation_activated(charm: Any) -> bool:
    """Report whether the database relation is established.

    Args:
        charm: the charm to inspect.

    Returns:
        True if at least one `database` relation exists.
    """
    return len(charm.model.relations[DATABASE_RELATION]) > 0


def is_legacy_database_relation_activated(charm: Any) -> bool:
    """Report whether the legacy database relation is established.

    Args:
        charm: the charm to inspect.

    Returns:
        True if at least one `database-legacy` relation exists.
    """
    return len(charm.model.relations[DATABASE_RELATION_LEGACY]) > 0


def check_database_relations(charm: Any) -> None:
    """Verify that only one of the two database relations is in use.

    Args:
        charm: the charm to inspect.

    Raises:
        BothDatabaseRelationsError: if both database relations are active.
    """
    if is_database_relation_activated(charm) and is_legacy_database_relation_activated(charm):
        raise BothDatabaseRelationsError(
            "Integration with both database relations is not allowed; "
            f"use either `{DATABASE_RELATION}` or `{DATABASE_RELATION_LEGACY}`."
        )


def database_uri(database: Any) -> Optional[str]:
    """Read the connection string from the database relation.

    Every unit can read the remote application databag directly, so the value
    is derived on each hook rather than relayed through peer relation data.
    Credentials delivered as Juju secrets are resolved by the library, which
    keeps them out of any databag this charm writes.

    ``fetch_relation_data`` iterates over active relations only, so this is
    safe to call while a `database-relation-broken` hook is running.

    Args:
        database: the `DatabaseRequires` instance to read from.

    Returns:
        The connection string, or None while the relation data is incomplete.
    """
    for fields in database.fetch_relation_data().values():
        username = fields.get("username")
        password = fields.get("password")
        endpoints = fields.get("endpoints")
        if username and password and endpoints:
            return build_database_uri(username, password, endpoints)
    return None


def metrics_db_uri(metrics_db: Any) -> Optional[str]:
    """Read the connection string from the metrics-db relation.

    Args:
        metrics_db: the `DatabaseRequires` instance for the metrics database.

    Returns:
        The connection string, or None while the relation data is incomplete.
    """
    for fields in metrics_db.fetch_relation_data().values():
        username = fields.get("username")
        password = fields.get("password")
        endpoints = fields.get("endpoints")
        if username and password and endpoints:
            return build_database_uri(username, password, endpoints, db_name=METRICS_DB_NAME)
    return None


def cve_catalog_url(charm: Any) -> Optional[str]:
    """Read the CVE catalog service address, if related.

    Args:
        charm: the charm to inspect.

    Returns:
        The CVE catalog service URL published by the remote application, or
        None when unavailable.
    """
    related = charm.model.relations.get(CVE_CATALOG_RELATION, None)
    if not related:
        return None
    return cve_source_url(related[0].data.get(related[0].app, {}))


def otel_metrics_endpoint(charm: Any) -> Optional[Tuple[str, str, bool]]:
    """Read the OTLP metrics push endpoint from the send-otlp relation, if related.

    Args:
        charm: the charm to inspect.

    Returns:
        A tuple of (endpoint, protocol, insecure), or None when unavailable.
    """
    related = charm.model.relations.get(SEND_OTLP_RELATION, None)
    if not related:
        return None
    return otlp_endpoint_from_data(related[0].data.get(related[0].app, {}))


def pro_airgapped_url(charm: Any) -> Optional[str]:
    """Read the air-gapped Pro contracts server address, if related.

    Args:
        charm: the charm to inspect.

    Returns:
        The contracts server URL, or None when unavailable.
    """
    relations = charm.model.relations.get(PRO_AIRGAPPED_SERVER_RELATION, None)
    if not relations:
        return None
    return _available_pro_airgapped_server_address(relations[0])


def _available_pro_airgapped_server_address(relation: Relation) -> Optional[str]:
    """Return the Pro air-gapped address taken from related unit databags.

    The returned value is the same on all units, which is achieved by iterating
    over a sorted list of the available units.

    Args:
        relation: the pro-airgapped-server relation.

    Returns:
        The contracts server URL, or None when no unit publishes a valid one.
    """
    for unit in sorted(relation.units, key=lambda unit: unit.name):
        data = relation.data.get(unit, None)
        if not data:
            continue
        address = airgapped_address_from_data(data)
        if address:
            return address
    return None


def push_outgoing(charm: Any, port: str) -> None:
    """Publish this unit's outgoing relation data.

    This runs on every reconcile rather than only on relation-joined, so the
    published data cannot go stale when the configuration changes.

    Args:
        charm: the charm publishing the data.
        port: the port the Livepatch server listens on.
    """
    for relation in charm.model.relations.get(WEBSITE_RELATION, []):
        try:
            relation.data[charm.unit]["port"] = port
        except ModelError as e:
            logger.warning("could not publish port on the `%s` relation: %s", WEBSITE_RELATION, e)
