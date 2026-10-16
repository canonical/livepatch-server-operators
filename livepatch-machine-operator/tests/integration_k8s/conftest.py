# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for the machine charm's cross-model observability integration tests.

These tests run on the K8s backend (``concierge-juju3.yaml``), which provisions
LXD and MicroK8s on a single controller. The machine charm under test is
deployed to the controller's default (LXD) model via pytest-jubilant's ``juju``
fixture; the OpenTelemetry Collector is deployed to a K8s model created here,
and the two are joined with a cross-model relation.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
from typing import Any, Dict, Iterable, Optional

import jubilant
import pytest
import pytest_jubilant
from integration.utils import resolve_charm

logger = logging.getLogger(__name__)

# Only honoured when set explicitly; otherwise the fixture discovers the cloud.
K8S_CLOUD = os.environ.get("JUJU_K8S_CLOUD")

# The single controller is bootstrapped on LXD (so the `localhost` credential is
# native) and the local K8s cluster is added to it as a secondary cloud by
# `_ensure_k8s_cloud`.
CROSS_MODEL_CONTROLLER = "concierge-lxd"
# Name under which the local K8s cluster is registered. Must not collide with
# Juju's built-in `k8s` cloud name.
K8S_SECONDARY_CLOUD = "k8scloud"

# Cloud types as reported by `juju clouds`, in the order they are preferred when
# several clouds of the same kind are registered.
K8S_CLOUD_TYPES = ("kubernetes", "k8s")
LXD_CLOUD_TYPES = ("lxd",)


@pytest.fixture(scope="session")
def charm() -> pathlib.Path:
    """Return the path of the charm under test."""
    return resolve_charm()


def _controller_clouds(controller: str) -> Dict[str, Dict[str, Any]]:
    """Return the clouds known to `controller`, keyed by cloud name."""
    juju = jubilant.Juju()
    return json.loads(juju.cli("clouds", "--controller", controller, "--format=json", include_model=False))


def _controller_cloud_type(controller: str) -> str:
    """Return the cloud type (e.g. "lxd", "k8s") that `controller` runs on."""
    juju = jubilant.Juju()
    info = json.loads(juju.cli("show-controller", controller, "--format=json", include_model=False))
    cloud = info[controller]["details"]["cloud"]
    return _controller_clouds(controller).get(cloud, {}).get("type", "")


def _run_juju(*args: str, stdin: Optional[str] = None) -> int:
    """Run a Juju CLI command for test setup, logging its combined output.

    Returns the exit code without raising. Used for the best-effort cloud
    provisioning steps, whose output we always want captured in the CI logs.
    """
    proc = subprocess.run(
        ["juju", *args], input=stdin, capture_output=True, encoding="utf-8", check=False
    )  # noqa: S603
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    logger.info("juju %s (exit=%s)\n%s", " ".join(args), proc.returncode, output)
    return proc.returncode


def _find_cloud(controller: str, cloud_types: Iterable[str], override: Optional[str]) -> str:
    """Return the name of a cloud of one of `cloud_types` known to `controller`.

    `override` always wins. Otherwise the cloud is discovered, because its name
    is not predictable: `concierge` registers Canonical K8s as `k8s`, while a
    local MicroK8s install registers as `microk8s`. `juju add-model` resolves the
    cloud against the controller, so only the controller's clouds are considered.
    """
    if override:
        return override

    clouds = _controller_clouds(controller)
    matches = [name for name, info in clouds.items() if str(info.get("type", "")) in cloud_types]
    found = {name: info.get("type") for name, info in clouds.items()}
    assert matches, (
        f"controller {controller!r} has no cloud of type {tuple(cloud_types)} "
        f"(found: {found}); set JUJU_K8S_CLOUD/JUJU_LXD_CLOUD"
    )
    # Prefer a deterministic name when several clouds of the same type exist.
    for preferred in (*cloud_types, "microk8s", "localhost"):
        if preferred in matches:
            return preferred
    return sorted(matches)[0]


def _ensure_k8s_cloud(controller: str) -> None:
    """Ensure a K8s cloud is registered with the (LXD-bootstrapped) controller.

    The controller is bootstrapped on LXD so the `localhost` LXD credential is
    native. The local MicroK8s cluster - taken from the kubeconfig that
    concierge writes - is registered as a secondary cloud with `juju add-k8s`.
    """
    if any(str(info.get("type", "")) in K8S_CLOUD_TYPES for info in _controller_clouds(controller).values()):
        return
    logger.info("Registering K8s cloud %r with controller %s", K8S_SECONDARY_CLOUD, controller)
    _run_juju("add-k8s", K8S_SECONDARY_CLOUD, "--client", "--controller", controller)


def _k8s_workload_storage() -> str:
    """Return the storage class for the cross-model K8s workload model.

    A K8s cloud added to an LXD controller is not given a usable default storage
    class, so one must be set explicitly. See:
      - https://bugs.launchpad.net/juju/+bug/2031216
      - https://bugs.launchpad.net/juju/+bug/2077426
    The cluster's default storage class is discovered via kubectl, falling back
    to `microk8s-hostpath`.
    """
    try:
        out = subprocess.run(  # noqa: S603
            ["kubectl", "get", "storageclass", "-o", "json"],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
        items = json.loads(out).get("items", [])
        for item in items:
            annotations = item.get("metadata", {}).get("annotations", {})
            if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                return item["metadata"]["name"]
        if items:
            return items[0]["metadata"]["name"]
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        logger.warning("Could not discover a K8s storage class: %s", exc)
    return "microk8s-hostpath"


@pytest.fixture(scope="module")
def k8s_juju(juju_factory: pytest_jubilant.JujuFactory) -> jubilant.Juju:
    """Return a Juju instance for a temporary K8s model (for cross-model tests).

    The single controller is bootstrapped on LXD, and the local MicroK8s cluster
    is added to it as a secondary cloud. Because a K8s cloud added to an LXD
    controller has no usable default storage class, the workload storage class
    is set explicitly.
    """
    controller = CROSS_MODEL_CONTROLLER
    _ensure_k8s_cloud(controller)
    cloud = _find_cloud(controller, K8S_CLOUD_TYPES, K8S_CLOUD)
    juju = juju_factory.get_juju(suffix="k8s", controller=controller, cloud=cloud)

    if _controller_cloud_type(controller) == "lxd":
        juju.model_config({"workload-storage": _k8s_workload_storage()})
    return juju
