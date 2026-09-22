import config_parity_lib


ALLOWED_FIELDS = {"presence", "type", "default", "description"}
_UNSET = object()


def _load_sources():
    return {
        name: config_parity_lib.load_options(source["path"], source["keys"])
        for name, source in config_parity_lib.CONFIG_SOURCES.items()
    }


def test_livepatch_charm_config_options_match():
    sources = _load_sources()

    violations = config_parity_lib.compare_options(sources["k8s"], sources["machine"])

    assert not violations, "Config parity violations:\n" + "\n".join(violations)


def test_intentional_differences_are_current_and_precise():
    sources = _load_sources()
    k8s_opts = sources["k8s"]
    machine_opts = sources["machine"]

    for option, fields in config_parity_lib.INTENTIONAL_DIFFERENCES.items():
        assert fields <= ALLOWED_FIELDS
        for field in fields:
            if field == "presence":
                assert (option in k8s_opts) != (option in machine_opts), option
                continue

            assert option in k8s_opts and option in machine_opts, option
            if field == "type":
                assert k8s_opts[option].get("type") != machine_opts[option].get("type"), option
            elif field == "default":
                k8s_default = k8s_opts[option].get("default", _UNSET)
                machine_default = machine_opts[option].get("default", _UNSET)
                assert k8s_default != machine_default, option
            elif field == "description":
                k8s_description = config_parity_lib.normalize_description(
                    k8s_opts[option].get("description")
                )
                machine_description = config_parity_lib.normalize_description(
                    machine_opts[option].get("description")
                )
                assert k8s_description != machine_description, option
