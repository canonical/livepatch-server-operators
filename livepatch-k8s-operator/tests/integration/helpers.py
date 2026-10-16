# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared constants and helpers for the Jubilant-based integration tests."""

import contextlib
import logging
import os
import re
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, Optional

import jubilant
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
POSTGRESQL_NAME = "postgresql-k8s"
POSTGRESQL_CHANNEL = "14/stable"
PRO_AIRGAPPED_SERVER_NAME = "pro-airgapped-server"
PRO_AIRGAPPED_SERVER_CHANNEL = "latest/stable"
PRO_AIRGAPPED_SERVER_ENDPOINT = "livepatch-server"
ACTIVE_STATUS = "active"
BLOCKED_STATUS = "blocked"
WAITING_STATUS = "waiting"

# Names defined by the charmcraft `go-framework` extension.
APP_IMAGE_RESOURCE = "app-image"
WORKLOAD_CONTAINER = "app"
WORKLOAD_SERVICE = "go"
APP_PORT = 8080

# Main database endpoint of the local (12-factor) charm, and the legacy name
# still used by the currently published stable charm on Charmhub.
DB_ENDPOINT = "postgresql"
LEGACY_DB_ENDPOINT = "database"

# OCI image used for the charm's `app-image` resource. A local charm deploy on
# a k8s model must always pass this explicitly, so the `upstream-source` in
# charmcraft.yaml is used as the default and `LIVEPATCH_APP_IMAGE` overrides it.
DEFAULT_APP_IMAGE = METADATA.get("resources", {}).get(APP_IMAGE_RESOURCE, {}).get("upstream-source")
APP_IMAGE = os.environ.get("LIVEPATCH_APP_IMAGE") or DEFAULT_APP_IMAGE

K8S_CLOUD = os.environ.get("JUJU_K8S_CLOUD")
LXD_CLOUD = os.environ.get("JUJU_LXD_CLOUD")
# Only honoured when set explicitly; otherwise the cross-model fixtures discover
# the cluster's default storage class. A K8s cloud added to an LXD controller is
# not given a usable default storage class, so one must be set manually.
K8S_WORKLOAD_STORAGE = os.environ.get("JUJU_K8S_WORKLOAD_STORAGE")

# The single controller is bootstrapped on LXD (so the `localhost` LXD
# credential is native). All K8s test modules run on it with the local K8s
# cluster added as a secondary cloud; the plain K8s modules deploy to that K8s
# cloud, and the cross-model modules additionally use the native `localhost`
# cloud for the machine side of their cross-model relations.
CROSS_MODEL_CONTROLLER = os.environ.get("JUJU_CROSS_MODEL_CONTROLLER", "concierge-lxd")
# Name under which the local K8s cluster is registered on the cross-model
# (LXD-bootstrapped) controller. Must not collide with Juju's built-in `k8s`
# cloud name.
K8S_SECONDARY_CLOUD = os.environ.get("JUJU_K8S_SECONDARY_CLOUD", "k8scloud")

# Cloud types as reported by `juju clouds`, in the order they are preferred when
# several clouds of the same kind are registered.
K8S_CLOUD_TYPES = ("kubernetes", "k8s")
LXD_CLOUD_TYPES = ("lxd",)

DEFAULT_TIMEOUT = 600

_ALL_STATUS: Dict[str, Callable[..., bool]] = {
    ACTIVE_STATUS: jubilant.all_active,
    BLOCKED_STATUS: jubilant.all_blocked,
    WAITING_STATUS: jubilant.all_waiting,
}


def wait_for_apps(
    juju: jubilant.Juju,
    apps: Iterable[str],
    status: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    successes: int = 3,
) -> jubilant.Status:
    """Wait for apps to settle, mirroring libjuju's `wait_for_idle` semantics.

    Waits until all units of `apps` have idle agents (and, if `status` is given,
    that workload status), raising immediately if any of them goes into error.
    Blocked units do not raise (matching `raise_on_blocked=False`).

    Args:
        juju: Juju instance operating on the target model.
        apps: Names of the applications to wait for.
        status: Expected workload status, or None to only wait for idle agents.
        timeout: Maximum time to wait, in seconds.
        successes: Number of consecutive successful polls (1s apart) required.

    Returns:
        The last observed status.
    """
    apps = list(apps)
    status_check = _ALL_STATUS[status] if status is not None else None

    def ready(s: jubilant.Status) -> bool:
        if status_check is not None and not status_check(s, *apps):
            return False
        return jubilant.all_agents_idle(s, *apps)

    return juju.wait(
        ready,
        error=lambda s: jubilant.any_error(s, *apps),
        timeout=timeout,
        successes=successes,
    )


def wait_for_app_removed(juju: jubilant.Juju, app: str, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Wait until an application no longer appears in the model status."""
    juju.wait(lambda s: app not in s.apps, timeout=timeout)


@contextlib.contextmanager
def fast_forward(juju: jubilant.Juju) -> Iterator[None]:
    """Temporarily speed up update-status hooks to fire every 10s (pytest-operator parity)."""
    old = juju.model_config()["update-status-hook-interval"]
    juju.model_config({"update-status-hook-interval": "10s"})
    try:
        yield
    finally:
        juju.model_config({"update-status-hook-interval": old})


def get_unit_url(juju: jubilant.Juju, application: str, unit: int, port: int, protocol: str = "http") -> str:
    """Return the unit URL of the form {protocol}://{address}:{port}."""
    address = juju.status().apps[application].units[f"{application}/{unit}"].address
    return f"{protocol}://{address}:{port}"


def extract_version_from_image(image: str) -> Optional[str]:
    """Return the version string (e.g. v1.2.3) in an OCI image reference, or None."""
    match = re.search(r"v[0-9]+(\.[0-9]+)*", image)
    return match.group(0) if match else None


def get_pebble_env(juju: jubilant.Juju, unit: str = f"{APP_NAME}/0") -> dict:
    """Return the environment dict from the workload service's current Pebble plan."""
    task = juju.exec(
        f"PEBBLE_SOCKET=/charm/containers/{WORKLOAD_CONTAINER}/pebble.socket /charm/bin/pebble plan",
        unit=unit,
    )
    plan = yaml.safe_load(task.stdout)
    return plan.get("services", {}).get(WORKLOAD_SERVICE, {}).get("environment", {})
