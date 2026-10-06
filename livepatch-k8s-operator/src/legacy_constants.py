# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Thin k8s-facing wrapper over the shared legacy-config migration map."""

import yaml

from livepatch_common.config_migration import migrate_config


def map_old_config_to_new_config(conf: dict) -> dict:
    """Convert old reactive charm config options to new ops charm config options."""
    settings = conf.get("options", {})
    if settings == {}:
        settings = conf.get("settings", {})
    if settings == {}:
        raise ValueError("No valid key for configuration found.")
    converted_options, removed_keys, unrecognized_keys = migrate_config(settings)
    return {
        # config file needs to have `canonical-livepatch-server-k8s` as the root in order to read properly.
        "new-config": yaml.dump({"canonical-livepatch-server-k8s": converted_options}).strip(),
        "removed-keys": removed_keys,
        "unrecognized-keys": unrecognized_keys,
    }
