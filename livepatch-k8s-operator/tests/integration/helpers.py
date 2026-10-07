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

# Default `app-image` OCI reference, sourced from the `resources` block in
# charmcraft.yaml (populated by the `go-framework` extension's expanded form).
# Juju fetches this automatically for both Charmhub and local-path deploys, so
# tests no longer need to pass it in explicitly (e.g. via an env var).
APP_IMAGE = METADATA.get("resources", {}).get(APP_IMAGE_RESOURCE, {}).get("upstream-source")

K8S_CLOUD = os.environ.get("JUJU_K8S_CLOUD", "microk8s")
LXD_CLOUD = os.environ.get("JUJU_LXD_CLOUD", "localhost")
K8S_WORKLOAD_STORAGE = os.environ.get("JUJU_K8S_WORKLOAD_STORAGE", "microk8s-hostpath")

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


def extract_version_from_image(image: str) -> str:
    """Extract the version string (e.g. v1.2.3) from an OCI image reference."""
    match = re.search(r"v[0-9]+(\.[0-9]+)*", image)
    if match:
        return match.group(0)
    raise ValueError(f"No version found in image reference {image!r}")


def get_pebble_env(juju: jubilant.Juju, unit: str = f"{APP_NAME}/0") -> dict:
    """Return the environment dict from the workload service's current Pebble plan."""
    task = juju.exec(
        f"PEBBLE_SOCKET=/charm/containers/{WORKLOAD_CONTAINER}/pebble.socket /charm/bin/pebble plan",
        unit=unit,
    )
    plan = yaml.safe_load(task.stdout)
    return plan.get("services", {}).get(WORKLOAD_SERVICE, {}).get("environment", {})
