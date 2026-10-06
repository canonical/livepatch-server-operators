# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Thin k8s-facing wrapper over the shared legacy-config migration map."""

from livepatch_common.config_migration import build_migrated_config

# Config file needs `canonical-livepatch-server-k8s` as the root in order to read properly.
CONFIG_ROOT = "canonical-livepatch-server-k8s"


def map_old_config_to_new_config(conf: dict) -> dict:
    """Convert old reactive charm config options to new ops charm config options."""
    return build_migrated_config(conf, config_root=CONFIG_ROOT)
