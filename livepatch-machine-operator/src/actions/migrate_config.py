# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Actions to migrate reactive and original Operator configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ops.charm import ActionEvent

from livepatch_common.config_migration import handle_migration_action

if TYPE_CHECKING:
    from charm import OperatorMachineCharm


def on_migrate_reactive_config_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Convert reactive options without applying the generated config."""
    handle_migration_action(
        event, source="reactive", config_root=self.app.name, supported_options=self.meta.config.keys()
    )


def on_migrate_ops_config_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Filter original Operator options against this charm's config keys."""
    handle_migration_action(event, source="ops", config_root=self.app.name, supported_options=self.meta.config.keys())
