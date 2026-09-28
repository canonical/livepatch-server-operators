# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Status Constants."""

# Statuses reported by the reconciler through `collect_unit_status`.
AWAIT_POSTGRES_RELATION = "Waiting for postgres relation to be established."
AWAIT_POSTGRES_URI = "Waiting for postgres..."
AWAIT_POSTGRES_PRIMARY = "Waiting for postgres to select primary node..."
SCHEMA_UPGRADE_FAILED = "Database schema upgrade failed, check logs."
LIVEPATCH_RUNNING = "Livepatch server running!"
LIVEPATCH_NOT_RUNNING = "Livepatch server is not running."
LIVEPATCH_FAILED_TO_INSTALL = "Livepatch server snap failed to install or refresh."
LIVEPATCH_FAILED_TO_CONFIGURE = "Livepatch server snap failed to apply configuration."
LIVEPATCH_FAILED_TO_RESTART = "Livepatch server failed to restart."
LIVEPATCH_FAILED_TO_UPDATE_CA = "Livepatch server failed to update the trusted CA certificate."
