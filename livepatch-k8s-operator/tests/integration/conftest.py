# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common pytest configuration and fixtures for integration tests."""

import json
import logging
import os
import pathlib
import subprocess
from typing import Dict, Iterable, Optional

import jubilant
import pytest
import pytest_jubilant
import yaml
from helpers import (
    APP_IMAGE,
    CROSS_MODEL_CONTROLLER,
    K8S_CLOUD,
    K8S_CLOUD_TYPES,
    K8S_SECONDARY_CLOUD,
    K8S_WORKLOAD_STORAGE,
    LXD_CLOUD,
    LXD_CLOUD_TYPES,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def app_image_resource(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Return the value to pass for the charm's `app-image` resource."""
    auth_path = os.environ.get("LIVEPATCH_APP_IMAGE_AUTH")
    if not auth_path or not pathlib.Path(auth_path).is_file():
        logger.info("Using %s as the app-image resource", APP_IMAGE)
        return APP_IMAGE

    auth = yaml.safe_load(pathlib.Path(auth_path).read_text()) or {}
    assert auth.get("username") and auth.get("password"), f"{auth_path} must define username and password"

    path = tmp_path_factory.mktemp("app-image") / "resource.yaml"
    path.write_text(yaml.safe_dump({"registrypath": APP_IMAGE, **auth}))
    path.chmod(0o600)
    logger.info("Using registry credentials from %s for %s", auth_path, APP_IMAGE)
    return str(path)


@pytest.fixture(scope="session")
def charm() -> pathlib.Path:
    """Return the path of the charm under test.

    The tests don't pack the charm. If CHARM_PATH is set, that .charm file is
    used, otherwise exactly one .charm file must exist in the charm root.
    """
    charm_path = os.environ.get("CHARM_PATH")
    if not charm_path:
        charm_dir = pathlib.Path()  # The current working directory is the charm root.
        charms = list(charm_dir.glob("*.charm"))
        assert charms, f"No charms were found in {charm_dir.absolute()}"
        assert len(charms) == 1, f"Found more than one charm {charms}"
        charm_path = str(charms[0])
    path = _resolve_charm_path(charm_path)
    assert path.is_file(), f"{path} is not a file"
    return path


def _resolve_charm_path(charm_path: str) -> pathlib.Path:
    """Resolve CHARM_PATH, which may be relative to the monorepo root.

    `opcli` renders the spread `pytest-environment-template` against
    artifacts.build.yaml, whose build paths are relative to the repository
    root, but it runs tox from the charm's `working-dir`. So a relative
    CHARM_PATH is tried against the current directory first and then against
    each ancestor.
    """
    path = pathlib.Path(charm_path)
    if path.is_absolute():
        return path.resolve()

    cwd = pathlib.Path.cwd()
    for base in (cwd, *cwd.parents):
        candidate = (base / path).resolve()
        if candidate.is_file():
            return candidate
    return (cwd / path).resolve()


def _controller_clouds(controller: str) -> Dict[str, dict]:
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
    proc = subprocess.run(["juju", *args], input=stdin, capture_output=True, encoding="utf-8")
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    logger.info("juju %s (exit=%s)\n%s", " ".join(args), proc.returncode, output)
    return proc.returncode


def _find_cloud(controller: str, cloud_types: Iterable[str], override: Optional[str]) -> str:
    """Return the name of a cloud of one of `cloud_types` known to `controller`.

    `override` (`JUJU_K8S_CLOUD`/`JUJU_LXD_CLOUD`) always wins. Otherwise the
    cloud is discovered, because its name is not predictable: `concierge`
    registers Canonical K8s as `k8s`, while a local MicroK8s install registers
    as `microk8s`. `juju add-model` resolves the cloud against the controller,
    so only the controller's clouds are considered.
    """
    if override:
        return override

    clouds = _controller_clouds(controller)
    matches = [name for name, info in clouds.items() if info.get("type") in cloud_types]
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

    The single controller is bootstrapped on LXD so that the `localhost` LXD
    credential is native. The K8s charm still needs a K8s cloud, so the local
    K8s cluster - taken from the kubeconfig that concierge writes - is registered
    as a secondary cloud with `juju add-k8s`. This mirrors the standalone repo's
    CI, which ran `juju add-cloud microk8s --controller <lxd-controller>
    --credential microk8s`.

    `--client` registers the cloud/credential in the local store as well, which
    Juju requires before (or while) adding it to the controller. The cloud name
    must not be Juju's reserved built-in `k8s`.
    """
    if any(info.get("type") in K8S_CLOUD_TYPES for info in _controller_clouds(controller).values()):
        return
    logger.info("Registering K8s cloud %r with controller %s", K8S_SECONDARY_CLOUD, controller)
    _run_juju("add-k8s", K8S_SECONDARY_CLOUD, "--client", "--controller", controller)


def _k8s_workload_storage() -> str:
    """Return the storage class for the cross-model K8s workload model.

    A K8s cloud added to an LXD controller is not given a usable default storage
    class, so one must be set explicitly. Check out these bug reports for more
    context:
      - https://bugs.launchpad.net/juju/+bug/2031216
      - https://bugs.launchpad.net/juju/+bug/2077426
    `JUJU_K8S_WORKLOAD_STORAGE` wins; otherwise the cluster's default storage
    class is discovered via kubectl, falling back to `microk8s-hostpath`.
    """
    if K8S_WORKLOAD_STORAGE:
        return K8S_WORKLOAD_STORAGE
    try:
        out = subprocess.run(
            ["kubectl", "get", "storageclass", "-o", "json"],
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


def _k8s_juju(juju_factory: pytest_jubilant.JujuFactory, suffix: str) -> jubilant.Juju:
    """Create a temporary K8s model on the (LXD-bootstrapped) cross-model controller.

    The single controller is bootstrapped on LXD (so the `localhost` credential
    is native), and the local K8s cluster is added to it as a secondary cloud.
    Because a K8s cloud added to an LXD controller has no usable default storage
    class, the workload storage class is set explicitly. Model teardown and log
    dumping are handled by `juju_factory`.
    """
    controller = CROSS_MODEL_CONTROLLER
    _ensure_k8s_cloud(controller)
    cloud = _find_cloud(controller, K8S_CLOUD_TYPES, K8S_CLOUD)
    juju = juju_factory.get_juju(suffix=suffix, controller=controller, cloud=cloud)

    if _controller_cloud_type(controller) == "lxd":
        juju.model_config({"workload-storage": _k8s_workload_storage()})
    return juju


@pytest.fixture(scope="module")
def juju(juju_factory: pytest_jubilant.JujuFactory) -> jubilant.Juju:
    """Override pytest-jubilant's default `juju` fixture for the K8s charm modules.

    The single controller is bootstrapped on LXD, so the plain K8s modules can't
    use the controller's default (LXD) cloud; this creates their model on the
    K8s cloud added to the controller, with the required workload storage class.
    """
    return _k8s_juju(juju_factory, suffix="")


@pytest.fixture(scope="module")
def k8s_juju(juju_factory: pytest_jubilant.JujuFactory) -> jubilant.Juju:
    """Return a Juju instance for a temporary K8s model (for cross-model tests)."""
    return _k8s_juju(juju_factory, suffix="k8s")


@pytest.fixture(scope="module")
def lxd_juju(juju_factory: pytest_jubilant.JujuFactory) -> jubilant.Juju:
    """Return a Juju instance for a temporary LXD model on the cross-model controller.

    The controller is bootstrapped on LXD, so the `localhost` credential is
    native and no cloud/credential provisioning is required here.
    """
    controller = CROSS_MODEL_CONTROLLER
    cloud = _find_cloud(controller, LXD_CLOUD_TYPES, LXD_CLOUD)
    return juju_factory.get_juju(suffix="lxd", controller=controller, cloud=cloud)
