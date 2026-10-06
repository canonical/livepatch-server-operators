# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the machine charm config migration script."""

import yaml

from scripts import migrate_config


def test_main_preserves_psql_dbname_mapping_for_machine_charm(tmp_path):
    input_file = tmp_path / "old_config.yaml"
    output_file = tmp_path / "new_config.yaml"

    input_file.write_text(
        yaml.safe_dump({"options": {"psql_dbname": {"value": "livepatch"}, "auth_lp_teams": {"value": "team"}}})
    )

    migrate_config.main(str(input_file), str(output_file))

    assert yaml.safe_load(output_file.read_text()) == {"options": {"database.name": "livepatch"}}
