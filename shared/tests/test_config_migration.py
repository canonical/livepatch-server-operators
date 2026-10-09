# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest

from livepatch_common.config_migration import migrate_config


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