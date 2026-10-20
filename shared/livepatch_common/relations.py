# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relation databag helpers shared by the livepatch charms.

These are pure functions over databag contents so they can be used from both
the k8s charm (which reads ``ops`` relations directly) and the machine charm
(which wraps them). No relation objects cross the boundary; callers pass the
unit/app databag they have already gathered.
"""

import json
import logging
from typing import Any, List, Mapping, Optional, Tuple
from urllib.parse import ParseResult, urlunparse

logger = logging.getLogger(__name__)

DATABASE_NAME = "livepatch"
METRICS_DB_NAME = "livepatch-metrics-db"


def build_database_uri(username: str, password: str, endpoints: str, db_name: str = DATABASE_NAME) -> str:
    """Compose a PostgreSQL connection string from database relation data.

    Args:
        username: the database username.
        password: the database password.
        endpoints: a comma-separated list of endpoints; the first is used.
        db_name: the database to connect to.

    Returns:
        The composed connection string.
    """
    endpoint = endpoints.split(",", 1)[0]
    # Reserved URI characters in either credential (`@`, `:`, `/`, ...) would
    # otherwise be misparsed as part of the URI's structure.
    from urllib.parse import quote

    return f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}@{endpoint}/{db_name}"


def strip_query_parameters(uri: str) -> str:
    """Drop query parameters from a PostgreSQL URI.

    Parameters such as `fallback_application_name` make the schema tool fail
    with `unrecognized configuration parameter (SQLSTATE 42704)`.

    Args:
        uri: the connection string to clean up.

    Returns:
        The connection string without its query parameters.
    """
    return uri.split("?", 1)[0]


def pro_airgapped_server_address(data: Mapping[str, Any]) -> Optional[str]:
    """Extract the Pro air-gapped server address from a unit databag.

    Args:
        data: the remote unit's databag.

    Returns:
        The contracts server URL, or None if the databag is not valid.
    """
    hostname = data.get("hostname")
    if not hostname:
        logger.error("empty 'hostname' value in pro-airgapped relation data")
        return None

    scheme = data.get("scheme") or "http"
    port = data.get("port")
    netloc = hostname + (f":{port}" if port else "")
    return urlunparse(ParseResult(scheme, netloc, "", "", "", ""))


def cve_source_url(data: Mapping[str, Any]) -> Optional[str]:
    """Read the CVE catalog service URL from an application databag.

    Args:
        data: the application/relation databag.

    Returns:
        The CVE catalog service URL, or None when unavailable.
    """
    url = data.get("url", "")
    return url or None


# Ranking used to pick between endpoints offering the same telemetry: newer
# transports win (mirrors the otlp charm lib's "favor modern protocol").
_OTLP_PROTOCOL_PRIORITY = {"grpc": 2, "http": 1}


def _otlp_endpoint_from_list(endpoints: List[Any]) -> Optional[Tuple[str, str, bool]]:
    """Pick the preferred metrics endpoint from the otlp ``endpoints`` list.

    Each item is a mapping ``{protocol, endpoint, telemetries, insecure}``.
    Only endpoints that accept ``metrics`` are considered, and when several
    remain the newest transport (``grpc`` over ``http``) is preferred.
    """
    candidates = [
        item
        for item in endpoints
        if isinstance(item, Mapping)
        and item.get("endpoint")
        and item.get("protocol")
        and "metrics" in (item.get("telemetries") or [])
    ]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda item: _OTLP_PROTOCOL_PRIORITY.get(str(item["protocol"]), 0),
    )
    return (
        str(best["endpoint"]),
        str(best["protocol"]),
        bool(best.get("insecure", False)),
    )


def otlp_metrics_endpoint(data: Mapping[str, Any]) -> Optional[Tuple[str, str, bool]]:
    """Read the OTLP metrics push endpoint from a send-otlp databag.

    Supports the current ``otlp`` interface, where the provider publishes an
    ``endpoints`` key holding a JSON list of ``{protocol, endpoint, telemetries,
    insecure}`` objects, as well as the legacy flat ``endpoint``/``protocol``/
    ``insecure`` keys for backwards compatibility.

    Args:
        data: the application/relation databag.

    Returns:
        A tuple of (endpoint, protocol, insecure), or None when unavailable.
    """
    raw_endpoints = data.get("endpoints")
    if raw_endpoints:
        endpoints = raw_endpoints
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except ValueError:
                logger.warning("ignoring malformed 'endpoints' in send-otlp databag")
                endpoints = []
        if isinstance(endpoints, list):
            resolved = _otlp_endpoint_from_list(endpoints)
            if resolved is not None:
                return resolved

    # Legacy flat schema.
    endpoint = data.get("endpoint")
    if not endpoint:
        return None
    protocol = data.get("protocol", "http")
    insecure = data.get("insecure", "true") == "true"
    return endpoint, protocol, insecure
