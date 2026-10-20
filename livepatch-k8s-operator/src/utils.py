# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utils module."""


def map_config_to_env_vars(charm, **additional_env):
    """
    Map the config values provided in config.yaml into environment variables.

    After that, the vars can be passed directly to the pebble layer.
    Variables must match the form LP_<Key1>_<key2>_<key3>...
    """
    env_mapped_config = {"LP_" + k.replace("-", "_").replace(".", "_").upper(): v for k, v in charm.config.items()}

    env_mapped_config["LP_SERVER_IS_LEADER"] = charm.unit.is_leader()

    return {**env_mapped_config, **additional_env}
