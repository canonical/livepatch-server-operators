# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Status Constants."""

LIVEPATCH_NOT_INSTALLED_ERROR = "Livepatch snap not installed, re-install the charm or run the install hook again."
CHECKING_DB_VERS = "Checking database schema version..."
INSTALLING = "Installing livepatch server..."
SUCCESSFUL_INSTALL = "Snap installed! Waiting on postgres relation..."
AWAIT_POSTGRES_RELATION = "Waiting for postgres relation to be established."

# Statuses reported by the reconciler through `collect_unit_status`.
AWAIT_POSTGRES_URI = "Waiting for postgres..."
AWAIT_POSTGRES_PRIMARY = "Waiting for postgres to select primary node..."
SCHEMA_UPGRADE_REQUIRED = "Database not initialised, please run the schema-upgrade action."
LIVEPATCH_RUNNING = "Livepatch running!"
LIVEPATCH_NOT_RUNNING = "Livepatch is not running."
LIVEPATCH_FAILED_TO_INSTALL = "Livepatch snap failed to install or refresh."
LIVEPATCH_FAILED_TO_CONFIGURE = "Livepatch snap failed to apply configuration."
LIVEPATCH_FAILED_TO_RESTART = "Livepatch failed to restart."
