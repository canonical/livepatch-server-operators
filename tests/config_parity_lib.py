from pathlib import Path
from typing import Any

import yaml


CONFIG_SOURCES = {
    "k8s": {"path": "livepatch-k8s-operator/charmcraft.yaml", "keys": ["config", "options"]},
    "machine": {"path": "livepatch-machine-operator/config.yaml", "keys": ["options"]},
}

INTENTIONAL_DIFFERENCES: dict[str, set[str]] = {
    # go-framework (paas-charm) plumbing options, k8s-only since the ejection of
    # the go-framework extension. The machine charm manages the snap directly and
    # has no framework app server.
    "app-port": {"presence"},
    "app-secret-key": {"presence"},
    "app-secret-key-id": {"presence"},
    "metrics-path": {"presence"},
    "metrics-port": {"presence"},
    "ingress-interface": {"presence"},
    "server.server-address": {"presence"},
    "patch-storage.filesystem-path": {"default"},
    # The machine charm ships a `set-basic-users` action (k8s does not), so its
    # description points operators at that action for bcrypt hashing.
    "auth.basic.users": {"description"},
    # k8s derives the OTLP endpoint from send-otlp; machine uses manual endpoint config.
    "otel-metrics.enabled": {"description"},
    # k8s uses workload identity federation; machine uses VM managed identity.
    "patch-storage.azure-managed-identity-client-id": {"description"},
    # k8s uses workload identity; machine can use VM-bound ambient ADC credentials.
    "patch-storage.gcs-credentials-file": {"description"},
    # k8s pods cannot use VM metadata directly; machine can use its default trusted profile.
    "patch-storage.ibm-trusted-profile-id": {"description"},
    # k8s uses IRSA/service-account credentials; machine can use VM instance credentials.
    "patch-storage.s3-access-key": {"description"},
    # k8s uses IRSA/service-account credentials; machine can use VM instance credentials.
    "patch-storage.s3-credentials-secret": {"description"},
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_UNSET = object()


def load_options(path: str, keys: list[str]) -> dict[str, dict]:
    """Load a charm's config options dict by walking nested `keys` in the YAML at `path`.
    `path` is relative to the repo root; resolve it robustly against the repo root regardless of CWD."""
    data: Any = yaml.safe_load((_REPO_ROOT / path).read_text(encoding="utf-8"))
    for key in keys:
        data = data[key]
    if not isinstance(data, dict):
        raise TypeError(f"{path}:{'.'.join(keys)} did not resolve to a dict")
    return data


def normalize_description(value):
    """Return None if value is None, else the description with the whole string stripped and each line rstripped, joined by \n."""
    if value is None:
        return None
    return "\n".join(line.rstrip() for line in str(value).strip().splitlines())


def compare_options(k8s_opts: dict, machine_opts: dict, allowlist: dict = INTENTIONAL_DIFFERENCES) -> list[str]:
    """Compare two option maps. Return a list of human-readable violation strings (empty list == full parity).
    Rules, each suppressible only via the allowlist for that option+field:
      - presence: an option present on only one side is a violation unless allowlisted with "presence".
      - type: differing `type` is a violation unless allowlisted with "type".
      - default: differing `default` is a violation unless allowlisted with "default". Treat 'unset on one side but set on the other' as a difference (unset must match unset). Use a sentinel to distinguish 'key absent' from an explicit null.
      - description: differing normalized description is a violation unless allowlisted with "description".
    Group/label each violation clearly, e.g. "[default] contracts.enabled: k8s=<unset> machine=False"."""
    violations = []

    for option in sorted(set(k8s_opts) | set(machine_opts)):
        allowed_fields = allowlist.get(option, set())
        k8s_present = option in k8s_opts
        machine_present = option in machine_opts

        if k8s_present != machine_present:
            if "presence" not in allowed_fields:
                side = "k8s" if k8s_present else "machine"
                violations.append(f"[presence] {option}: only in {side}")
            continue

        k8s_option = k8s_opts[option]
        machine_option = machine_opts[option]

        if k8s_option.get("type") != machine_option.get("type") and "type" not in allowed_fields:
            violations.append(
                f"[type] {option}: k8s={_format_value(k8s_option.get('type'))} "
                f"machine={_format_value(machine_option.get('type'))}"
            )

        k8s_default = k8s_option.get("default", _UNSET)
        machine_default = machine_option.get("default", _UNSET)
        if k8s_default != machine_default and "default" not in allowed_fields:
            violations.append(
                f"[default] {option}: k8s={_format_value(k8s_default)} "
                f"machine={_format_value(machine_default)}"
            )

        k8s_description = normalize_description(k8s_option.get("description"))
        machine_description = normalize_description(machine_option.get("description"))
        if k8s_description != machine_description and "description" not in allowed_fields:
            violations.append(
                f"[description] {option}: k8s={_format_value(k8s_description)} "
                f"machine={_format_value(machine_description)}"
            )

    return violations


def _format_value(value) -> str:
    if value is _UNSET:
        return "<unset>"
    return repr(value)
