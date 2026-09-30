# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livepatch_common.config_migration import migrate_config


def parse_input_file(input_file_path: str):
    with open(input_file_path, "r") as yaml_stream:
        yaml_data = yaml.load(yaml_stream, Loader=yaml.FullLoader)
        return yaml_data


if "psql_dbname" not in settings:
        return
    psql_config = settings["psql_dbname"]
    if "value" not in psql_config or psql_config["value"] is None:
        raise ValueError("psql_dbname doesn't have a set value for it")
    psql_dbname = psql_config["value"]
    if psql_dbname == "":
        return

    converted_options["database.name"] = psql_dbname
    if "psql_dbname" in removed_keys:
        removed_keys.remove("psql_dbname")


def main(input_file, output_file):
    json_content = parse_input_file(input_file)
    settings = json_content.get("options", {})
    if settings == {}:
        settings = json_content.get("settings", {})
    if settings == {}:
        print("No settings found")
        exit(-1)
    converted_options, removed_keys, unrecognized_keys = migrate_config(settings)
    _restore_machine_only_migrations(settings, converted_options, removed_keys)
    print(
        "These keys were present in your configuration",
        "but they are removed for the new config.\n",
        removed_keys,
    )
    print("\nUnrecognized keys: ", unrecognized_keys)
    new_config = {"options": converted_options}
    with open(f"{output_file}", "w") as f:
        yaml.dump(new_config, f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process input and write to output file")
    parser.add_argument("-i", "--input_file", help="input file full path")
    parser.add_argument("-o", "--output_file", help="output file full path")
    args = parser.parse_args()

    main(args.input_file, args.output_file)