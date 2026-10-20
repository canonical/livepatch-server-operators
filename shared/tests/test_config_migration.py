# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
import yaml
from livepatch_common import config_migration
from livepatch_common.config_migration import build_migrated_config, migrate_config


def test_migrate_config_maps_known_options():
    converted, removed, unrecognized = migrate_config(
        {
            "auth_basic_users": {"value": "admin:h"},
            "burst_limit": {"value": "100"},
            "url_template": {"value": "https://example.com/{filename}"},
        }
    )
    assert converted == {
        "auth.basic.enabled": True,
        "auth.basic.users": "admin:h",
        "server.burst-limit": "100",
        "server.url-template": "https://example.com/{filename}",
    }
    assert removed == []
    assert unrecognized == []


def test_migrate_config_flags_removed_keys():
    converted, removed, unrecognized = migrate_config(
        {
            "auth_lp_teams": {"value": "team"},
            "psql_dbname": {"value": "livepatch"},
            "event_bus_brokers": {"value": "kafka:9092"},
        }
    )
    assert converted == {}
    assert removed == ["auth_lp_teams", "psql_dbname", "event_bus_brokers"]
    assert unrecognized == []


def test_migrate_config_flags_unrecognized_keys():
    _, _, unrecognized = migrate_config({"some_unknown_option": {"value": "x"}})
    assert unrecognized == ["some_unknown_option"]


def test_migrate_config_applies_override_value():
    converted, _, _ = migrate_config({"patchstore": {"value": "file"}})
    assert converted["patch-storage.type"] == "filesystem"


def test_migrate_config_adds_derived_additional_values():
    converted, _, _ = migrate_config(
        {
            "sync_token": {"value": "token"},
            "http_proxy": {"value": "http://proxy"},
        }
    )
    assert converted["patch-sync.enabled"] is True
    assert converted["patch-sync.token"] == "token"
    assert converted["patch-sync.proxy.enabled"] is True
    assert converted["patch-sync.proxy.http"] == "http://proxy"


@pytest.mark.parametrize("key", ["auth_basic_users", "blocklist_cache_refresh", "filebacked"])
def test_migrate_config_additional_key_requires_value(key):
    converted, _, _ = migrate_config({key: {"value": ""}})
    assert "auth.basic.enabled" not in converted
    assert "patch-blocklist.enabled" not in converted
    assert "patch-storage.type" not in converted


def test_migrate_config_raises_when_value_missing():
    with pytest.raises(ValueError, match="doesn't have a set value"):
        migrate_config({"burst_limit": {}})


def test_migrate_config_skips_empty_value_for_mapped_key():
    converted, _, _ = migrate_config({"burst_limit": {"value": ""}})
    assert "server.burst-limit" not in converted


def test_build_migrated_config_nests_under_config_root():
    import yaml

    result = build_migrated_config(
        {
            "options": {
                "burst_limit": {"value": "100"},
                "auth_lp_teams": {"value": "team"},
            }
        },
        config_root="my-charm",
    )
    assert yaml.safe_load(result["new-config"]) == {"my-charm": {"server.burst-limit": "100"}}
    assert result["removed-keys"] == ["auth_lp_teams"]
    assert result["unrecognized-keys"] == []


def test_build_migrated_config_falls_back_to_settings_key():
    import yaml

    result = build_migrated_config(
        {"settings": {"burst_limit": {"value": "100"}}},
        config_root="my-charm",
    )
    assert yaml.safe_load(result["new-config"]) == {"my-charm": {"server.burst-limit": "100"}}


def test_build_migrated_config_applies_extra_mappings():
    import yaml

    result = build_migrated_config(
        {"options": {"psql_dbname": {"value": "livepatch"}}},
        config_root="my-charm",
        extra_mappings={"psql_dbname": "database.name"},
    )
    assert yaml.safe_load(result["new-config"]) == {"my-charm": {"database.name": "livepatch"}}
    assert result["removed-keys"] == []


def test_build_migrated_config_extra_mappings_skip_empty_value():
    import yaml

    result = build_migrated_config(
        {"options": {"psql_dbname": {"value": ""}}},
        config_root="my-charm",
        extra_mappings={"psql_dbname": "database.name"},
    )
    assert yaml.safe_load(result["new-config"]) == {"my-charm": {}}
    assert result["removed-keys"] == ["psql_dbname"]


def test_build_migrated_config_raises_when_no_settings():
    with pytest.raises(ValueError, match="No valid key for configuration found."):
        build_migrated_config({"something-else": {}}, config_root="my-charm")


@pytest.mark.parametrize(
    "document",
    [
        {"options": ["bad"]},
        {"options": {"burst_limit": None}},
    ],
)
def test_build_migrated_config_rejects_malformed_settings(document):
    with pytest.raises(ValueError, match="must be a mapping"):
        build_migrated_config(document, config_root="my-charm")


@pytest.mark.parametrize("root", ["options", "settings"])
def test_ops_migration_preserves_values_and_reports_unsupported_options(root):
    result = config_migration.build_ops_migrated_config(
        {
            root: {
                "patch-sync.enabled": {"value": False, "default": True},
                "server.burst-limit": {"value": 0},
                "server.url-template": {"value": ""},
                "server.log-level": {"value": "warn", "source": "default"},
                "auth.basic.users": {"value": None},
                "influx.token": {},
                "auth.sso.enabled": {"value": True},
                "cloud-delay.default-delay-hours": {"value": 0},
                "machine-reports.event-bus.brokers": {"value": "kafka:9092"},
                "future.option": {"value": "unknown"},
            }
        },
        config_root="custom-app",
        supported_options={
            "patch-sync.enabled",
            "server.burst-limit",
            "server.url-template",
            "server.log-level",
            "auth.basic.users",
            "influx.token",
        },
    )
    assert yaml.safe_load(result["new-config"]) == {
        "custom-app": {
            "patch-sync.enabled": False,
            "server.burst-limit": 0,
            "server.url-template": "",
            "server.log-level": "warn",
        }
    }
    assert result["removed-keys"] == [
        "auth.sso.enabled",
        "cloud-delay.default-delay-hours",
        "machine-reports.event-bus.brokers",
    ]
    assert result["unrecognized-keys"] == ["future.option"]


def test_ops_migration_uses_destination_options_not_reactive_map():
    result = config_migration.build_ops_migrated_config(
        {"settings": {"destination.new-option": {"value": "keep"}}},
        config_root="custom-app",
        supported_options={"destination.new-option"},
    )
    assert yaml.safe_load(result["new-config"]) == {"custom-app": {"destination.new-option": "keep"}}


@pytest.mark.parametrize("key", ["auth.sso.typo", "cloud-delay.typo", "machine-reports.event-bus.typo"])
def test_ops_migration_does_not_mislabel_unknown_keys_as_removed(key):
    result = config_migration.build_ops_migrated_config(
        {"settings": {"server.log-level": {"value": "info"}, key: {"value": "x"}}},
        config_root="custom-app",
        supported_options={"server.log-level"},
    )
    assert result["removed-keys"] == []
    assert result["unrecognized-keys"] == [key]


@pytest.mark.parametrize("source", ["reactive", "ops"])
@pytest.mark.parametrize(
    "document",
    [
        {"settings": {"burst_limit": {"value": 1}, "server.burst-limit": {"value": 1}}},
        {"settings": {42: {"value": "bad"}}},
        {"settings": {"burst_limit": {"value": []}}},
        {"settings": {"server.burst-limit": {"value": {}}}},
    ],
)
def test_migration_rejects_malformed_or_mixed_exports(source, document):
    with pytest.raises(ValueError):
        if source == "reactive":
            build_migrated_config(document, config_root="app")
        else:
            config_migration.build_ops_migrated_config(
                document,
                config_root="app",
                supported_options={"server.burst-limit"},
            )


def test_reactive_migration_rejects_operator_export():
    with pytest.raises(ValueError, match="migrate-ops-config"):
        build_migrated_config(
            {"settings": {"server.burst-limit": {"value": 50}}},
            config_root="app",
        )


def test_ops_migration_rejects_reactive_export():
    with pytest.raises(ValueError, match="migrate-reactive-config"):
        config_migration.build_ops_migrated_config(
            {"settings": {"burst_limit": {"value": 50}}},
            config_root="app",
            supported_options={"server.burst-limit"},
        )


@pytest.mark.parametrize("source", ["reactive", "ops"])
def test_migration_rejects_competing_export_containers(source):
    document = {
        "options": {"server.log-level": {"value": "info"}},
        "settings": {"auth_basic_users": {"value": "user:hash"}},
    }
    with pytest.raises(ValueError, match="both"):
        if source == "reactive":
            build_migrated_config(document, config_root="app")
        else:
            config_migration.build_ops_migrated_config(
                document, config_root="app", supported_options={"server.log-level"}
            )


def test_reactive_migration_reports_unknown_dotted_keys():
    result = build_migrated_config(
        {"settings": {"burst_limit": {"value": 50}, "custom.option": {"value": "x"}}},
        config_root="app",
    )
    assert yaml.safe_load(result["new-config"]) == {"app": {"server.burst-limit": 50}}
    assert result["unrecognized-keys"] == ["custom.option"]


def test_reactive_migration_rejects_operator_keys_from_destination_metadata():
    with pytest.raises(ValueError, match="migrate-ops-config"):
        build_migrated_config(
            {"settings": {"destination.new-option": {"value": "x"}}},
            config_root="app",
            supported_options={"destination.new-option"},
        )
