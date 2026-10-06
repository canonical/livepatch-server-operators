# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Action to convert an exported reactive-charm config to the modern format."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from ops.charm import ActionEvent

from livepatch_common.config_migration import build_migrated_config

if TYPE_CHECKING:
    from charm import OperatorMachineCharm

# The application name the generated config must be nested under for juju to read it.
CONFIG_ROOT = "canonical-livepatch-server"

# `psql_dbname` is dropped by the shared migration map because the k8s charm
# takes its database name from a relation. The machine charm has always mapped
# it to `database.name`, so it is restored here for backwards compatibility with
# configs exported from the old reactive machine charm. Kept even though the
# current machine charm has no `database.name` config option, so migrations of
# older deployments don't silently lose the value.
EXTRA_MAPPINGS = {"psql_dbname": "database.name"}


def on_emit_updated_config_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Convert a legacy reactive-charm config export to the modern ops config.

    The `config-file` param carries the *contents* of the old charm's exported
    config. This action does not change the running configuration; it returns
    the converted options plus the keys that were removed or not recognised, for
    the operator to apply themselves with `juju config`.
    """
    config_content = event.params["config-file"]
    try:
        config_yaml = yaml.safe_load(config_content)
        if not (isinstance(config_yaml, dict) and config_yaml.keys()):
            event.fail(f"invalid config file format. Got content {config_content}")
            return
    except yaml.YAMLError as e:
        event.fail(f"Failed to parse YAML: {e}")
        return
    try:
        result = build_migrated_config(config_yaml, config_root=CONFIG_ROOT, extra_mappings=EXTRA_MAPPINGS)
    except ValueError as e:
        event.fail(f"Failed to map old config to new config: {e}")
        return
    event.set_results({"result": result})
