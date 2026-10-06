# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Legacy reactive-charm config to modern ops config migration.

Single source of truth for both livepatch charms' migration maps. The k8s
charm's map is authoritative where the two once disagreed; the machine
charm's entries that the k8s map never covered (influxdb_*, event_bus_*) are
folded in so neither charm regresses.
"""

from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Config map for converting from old reactive charm configs to the modern config format.
CONFIG_MAP = {
    # Authentication
    "auth_basic_users": "auth.basic.users",
    "auth_lp_teams": None,
    "auth_sso_location": None,
    "auth_sso_public_key": None,
    # Server
    "burst_limit": "server.burst-limit",
    "concurrency_limit": "server.concurrency-limit",
    "log_level": "server.log-level",
    "url_template": "server.url-template",
    # Database / connection pool
    "dbconn_max_lifetime": "database.connection-lifetime-max",
    "dbconn_max": "database.connection-pool-max",
    "psql_dbname": None,
    "psql_roles": None,
    # Patch sync
    "patch_sync_enabled": "patch-sync.enabled",
    "sync_flavors": "patch-sync.flavors",
    "sync_interval": "patch-sync.interval",
    "sync_minimum_kernel_version": "patch-sync.minimum-kernel-version",
    "sync_architectures": "patch-sync.architectures",
    "sync_token": "patch-sync.token",  # nosec
    "sync_upstream": "patch-sync.upstream-url",
    "sync_send_machine_reports": "patch-sync.send-machine-reports",
    "sync_identity": None,
    "sync_tier": None,
    "sync_upstream_tier": None,
    # Patch sync proxies
    "http_proxy": "patch-sync.proxy.http",
    "https_proxy": "patch-sync.proxy.https",
    "no_proxy": "patch-sync.proxy.no-proxy",
    # Patch storage
    "patchstore": "patch-storage.type",
    "storage_path": "patch-storage.filesystem-path",
    "s3_access_key_id": "patch-storage.s3-access-key",
    "s3_bucket": "patch-storage.s3-bucket",
    "s3_endpoint": "patch-storage.s3-endpoint",
    "s3_region": "patch-storage.s3-region",
    "s3_secret_key": "patch-storage.s3-secret-key",  # nosec
    "s3_secure": "patch-storage.s3-secure",
    "swift_apikey": "patch-storage.swift-api-key",
    "swift_auth_url": "patch-storage.swift-auth-url",
    "swift_container_name": "patch-storage.swift-container",
    "swift_domain_name": "patch-storage.swift-domain",
    "swift_region_name": "patch-storage.swift-region",
    "swift_tenant_name": "patch-storage.swift-tenant",
    "swift_username": "patch-storage.swift-username",
    # Patch cache
    "patch_cache_on": "patch-cache.enabled",
    "patch_cache_size": "patch-cache.cache-size",
    "patch_cache_ttl": "patch-cache.cache-ttl",
    # Blocklist cache
    "blocklist_cache_refresh": "patch-blocklist.refresh-interval",
    # Machine reports
    "report_cleanup_interval": "machine-reports.database.cleanup-interval",
    "report_cleanup_row_limit": "machine-reports.database.cleanup-row-limit",
    "report_retention": "machine-reports.database.retention-days",
    # KPI reports
    "kpi_reports": "kpi-reports.interval",
    # Contracts
    "contract_server_password": "contracts.password",  # nosec
    "contract_server_url": "contracts.url",
    "contract_server_user": "contracts.user",
    # Profiler settings
    "profiler_block_profile_rate": "profiler.block-profile-rate",
    "profiler_enabled": "profiler.enabled",
    "profiler_hostname": "profiler.hostname",
    "profiler_mutex_profile_fraction": "profiler.mutex-profile-fraction",
    "profiler_profile_allocations": "profiler.profile-allocations",
    "profiler_profile_blocks": "profiler.profile-blocks",
    "profiler_profile_goroutines": "profiler.profile-goroutines",
    "profiler_profile_inuse": "profiler.profile-inuse",
    "profiler_profile_mutexes": "profiler.profile-mutexes",
    "profiler_sample_rate": "profiler.sample-rate",
    "profiler_server_address": "profiler.server-address",
    "profiler_upload_rate": "profiler.upload-rate",
    # Influx (machine charm only; k8s map never covered these)
    "influxdb_bucket": "influx.bucket",
    "influxdb_organization": "influx.organization",
    "influxdb_token": "influx.token",
    "influxdb_url": "influx.url",
    # Event bus (no longer used)
    "event_bus_brokers": None,
    "event_bus_ca_cert": None,
    "event_bus_client_cert": None,
    "event_bus_client_key": None,
    # Cloud delay (no longer used)
    "cloud_delay_default_delay_hours": None,
    "is_cloud_delay_enabled": None,
}
# Additional config map for enabling certain features based on if a value is set.
ADDITIONAL_CONFIG_MAP = {
    "auth_basic_users": ("auth.basic.enabled", True),
    "blocklist_cache_refresh": ("patch-blocklist.enabled", True),
    "contract_server_password": ("contracts.enabled", True),
    "contract_server_user": ("contracts.enabled", True),
    "filebacked": ("patch-storage.type", "filesystem"),
    "http_proxy": ("patch-sync.proxy.enabled", True),
    "https_proxy": ("patch-sync.proxy.enabled", True),
    "kpi_reports": ("kpi-reports.enabled", True),
    "no_proxy": ("patch-sync.proxy.enabled", True),
    "sync_architectures": ("patch-sync.enabled", True),
    "sync_flavors": ("patch-sync.enabled", True),
    "sync_interval": ("patch-sync.enabled", True),
    "sync_minimum_kernel_version": ("patch-sync.enabled", True),
    "sync_token": ("patch-sync.enabled", True),
    "sync_upstream": ("patch-sync.enabled", True),
}

# Values we override with new names, i.e file -> filesystem
OVERRIDE_VALUES = {
    "patch-storage.type": ("file", "filesystem"),
}

# Sentinel object used to optimize dict lookups.
_MISSING = object()


def __map_legacy_config(
    key: str, parsed_val: Any, unrecognized_keys: List[str], removed_keys: List[str], converted_options: Dict[str, Any]
) -> None:
    """Migrate a legacy configuration key to the new format.

    Depending on the mapping logic, the result is appended in-place to one of the provided output lists.
    - Valid mappings are appended to `converted_options`.
    - Unknown keys are appended to `unrecognized_keys`.
    - Deprecated keys are appended to `removed_keys`.
    """
    # Set additional config entries only if the key has a non-empty value.
    if parsed_val not in [None, ""]:
        additional_map_v = ADDITIONAL_CONFIG_MAP.get(key)
        if additional_map_v:
            add_conf_key, add_conf_val = additional_map_v
            converted_options[add_conf_key] = add_conf_val

    # ensure we check if key exists and for truthiness.
    val = CONFIG_MAP.get(key, _MISSING)
    if val is _MISSING:
        unrecognized_keys.append(key)
    elif not val:
        removed_keys.append(key)
    else:
        if parsed_val is None:
            raise ValueError(f"{key} doesn't have a set value for it")
        if parsed_val == "":
            return

        converted_options[val] = parsed_val  # type: ignore


def migrate_config(settings: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Convert a legacy reactive charm config option map to the new format.

    Args:
        settings: the raw ``options``/``settings`` mapping from a legacy
            reactive-charm config.

    Returns:
        A tuple of the converted options, the keys that were removed, and the
        keys that were not recognised.

    Raises:
        ValueError: if a mapped option has no configured value.
    """
    converted_options: Dict[str, Any] = {}
    removed_keys: List[str] = []
    unrecognized_keys: List[str] = []

    for key, val in settings.items():
        parsed_val = val.get("value", None)
        __map_legacy_config(key, parsed_val, unrecognized_keys, removed_keys, converted_options)

    for key, value in OVERRIDE_VALUES.items():
        if key in converted_options:
            current, override = value
            if converted_options[key] == current:
                converted_options[key] = override
    return converted_options, removed_keys, unrecognized_keys


def _apply_extra_mappings(
    settings: Dict[str, Dict[str, Any]],
    converted_options: Dict[str, Any],
    removed_keys: List[str],
    extra_mappings: Dict[str, str],
) -> None:
    """Restore charm-specific legacy mappings that the shared map drops.

    Each ``legacy_key -> new_key`` pair is re-applied when the legacy key is
    present with a usable value, and the legacy key is pulled back out of
    ``removed_keys``. This exists for options a single charm still honours for
    backwards compatibility even though the shared map marks them removed.

    Args:
        settings: the raw legacy ``options``/``settings`` mapping.
        converted_options: the already-converted options, mutated in place.
        removed_keys: the list of removed keys, mutated in place.
        extra_mappings: the ``legacy_key -> new_key`` pairs to restore.

    Raises:
        ValueError: if a restored legacy key is present without a value.
    """
    for legacy_key, new_key in extra_mappings.items():
        if legacy_key not in settings:
            continue
        value = settings[legacy_key].get("value")
        if value is None:
            raise ValueError(f"{legacy_key} doesn't have a set value for it")
        if value == "":
            continue
        converted_options[new_key] = value
        if legacy_key in removed_keys:
            removed_keys.remove(legacy_key)


def build_migrated_config(
    document: Dict[str, Any],
    *,
    config_root: str,
    extra_mappings: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Convert an exported reactive-charm config document into action results.

    This is the charm-facing entry point shared by both livepatch charms'
    config-migration actions. It parses the exported document, runs the shared
    migration map, applies any charm-specific ``extra_mappings``, and serialises
    the result under the charm's application name.

    Args:
        document: the parsed legacy config, a mapping carrying an ``options`` or
            ``settings`` key (as exported by the old reactive charms).
        config_root: the application name the new config must be nested under,
            e.g. ``canonical-livepatch-server`` so ``juju config`` can read it.
        extra_mappings: optional ``legacy_key -> new_key`` pairs to restore on
            top of the shared map, for options a single charm still supports.

    Returns:
        A mapping with the serialised ``new-config`` YAML and the
        ``removed-keys``/``unrecognized-keys`` lists.

    Raises:
        ValueError: if the document has no ``options``/``settings`` mapping, or
            a mapped option has no configured value.
    """
    settings = document.get("options") or document.get("settings")
    if not settings:
        raise ValueError("No valid key for configuration found.")
    if not isinstance(settings, Mapping):
        raise ValueError("Configuration options/settings must be a mapping.")
    for key, option in settings.items():
        if not isinstance(option, Mapping):
            raise ValueError(f"Configuration option {key!r} must be a mapping.")
    converted_options, removed_keys, unrecognized_keys = migrate_config(settings)
    if extra_mappings:
        _apply_extra_mappings(settings, converted_options, removed_keys, extra_mappings)
    return {
        "new-config": yaml.dump({config_root: converted_options}).strip(),
        "removed-keys": removed_keys,
        "unrecognized-keys": unrecognized_keys,
    }