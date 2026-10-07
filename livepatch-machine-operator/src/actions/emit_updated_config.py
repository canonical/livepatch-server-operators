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
            event.fail("invalid config file format")
            return
    except yaml.YAMLError as e:
        event.fail(f"Failed to parse YAML: {e}")
        return
    try:
        result = build_migrated_config(config_yaml, config_root=CONFIG_ROOT)
    except ValueError as e:
        event.fail(f"Failed to map old config to new config: {e}")
        return
    event.set_results({"result": result})
