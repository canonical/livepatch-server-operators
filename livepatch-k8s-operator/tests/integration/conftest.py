# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common pytest configuration and fixtures for integration tests."""

import json
import logging
import os
import pathlib
from typing import Dict, Iterable, Iterator, Optional

import jubilant
import pytest
import pytest_jubilant
import yaml
from helpers import APP_IMAGE, K8S_CLOUD, K8S_CLOUD_TYPES, K8S_WORKLOAD_STORAGE, LXD_CLOUD, LXD_CLOUD_TYPES

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


def _controller_info(juju: jubilant.Juju) -> tuple:
    """Return the (name, details) of the current controller.

    `show-controller` is used rather than `show-model` so that this works
    before any model exists.
    """
    info = json.loads(juju.cli("show-controller", "--format=json", include_model=False))
    name = next(iter(info))
    return name, info[name]


def _controller_clouds(juju: jubilant.Juju) -> Dict[str, dict]:
    """Return the clouds known to the current controller, keyed by cloud name."""
    name, _ = _controller_info(juju)
    return json.loads(juju.cli("clouds", "--controller", name, "--format=json", include_model=False))


def _controller_cloud_type(juju: jubilant.Juju) -> str:
    """Return the cloud type (e.g. "lxd", "k8s") of the cloud the controller runs on."""
    _, details = _controller_info(juju)
    cloud = details["details"]["cloud"]
    return _controller_clouds(juju).get(cloud, {}).get("type", "")


def _ensure_lxd_cloud(juju: jubilant.Juju) -> None:
    """Ensure an LXD cloud is registered with the current controller.

    In CI environments (e.g. concierge), the controller is bootstrapped on k8s
    and initially only knows about the k8s cloud, even though LXD is installed on
    the host. Adding 'localhost' (type lxd) to the controller enables cross-model
    relations between k8s and machine charms under the same controller.
    """
    clouds = _controller_clouds(juju)
    if any(info.get("type") in LXD_CLOUD_TYPES for info in clouds.values()):
        return

    controller, _ = _controller_info(juju)
    logger.info("Registering LXD cloud 'localhost' with controller %s", controller)
    try:
        juju.cli("add-cloud", "--controller", controller, "localhost", include_model=False)
    except Exception as exc:
        logger.warning("Could not add LXD cloud 'localhost' to controller %s: %s", controller, exc)
        return

    _ensure_lxd_credential(juju, controller)


def _ensure_lxd_credential(juju: jubilant.Juju, controller: str) -> None:
    """Upload a credential for the 'localhost' LXD cloud to the controller.

    `juju add-cloud --controller` only copies the cloud definition. concierge
    adds LXD as a client-side cloud but writes no credential (its LXD provider's
    credentials are empty), so without this step `juju add-model ... localhost`
    fails with `cloud credential "localhost/admin/localhost" not found`.

    `autoload-credentials` detects the local LXD setup (generating a client
    certificate and trusting it with the LXD daemon) and, when given both
    `--client` and `--controller`, uploads the resulting credential to the
    controller. The interactive prompts are answered over stdin: select the
    first detected credential, accept the default cloud name (empty line), then
    quit the loop with `q`, which triggers the controller upload. This assumes
    LXD's 'localhost' is the only auto-detected credential, which holds on the
    self-hosted CI runners.
    """
    logger.info("Uploading LXD credential to controller %s", controller)
    try:
        juju.cli(
            "autoload-credentials",
            "--client",
            "--controller",
            controller,
            include_model=False,
            stdin="1\n\nq\n",
        )
    except Exception as exc:
        logger.warning("Could not upload an LXD credential to controller %s: %s", controller, exc)


def _find_cloud(juju: jubilant.Juju, cloud_types: Iterable[str], override: Optional[str]) -> str:
    """Return the name of a cloud of one of `cloud_types` known to the controller.

    `override` (`JUJU_K8S_CLOUD`/`JUJU_LXD_CLOUD`) always wins. Otherwise the
    cloud is discovered, because its name is not predictable: `concierge`
    registers Canonical K8s as `k8s`, while a local MicroK8s install registers
    as `microk8s`. `juju add-model` resolves the cloud against the controller,
    so only the controller's clouds are considered.
    """
    if override:
        return override

    clouds = _controller_clouds(juju)
    matches = [name for name, info in clouds.items() if info.get("type") in cloud_types]
    found = {name: info.get("type") for name, info in clouds.items()}
    assert matches, (
        f"the controller has no cloud of type {tuple(cloud_types)} "
        f"(found: {found}); set JUJU_K8S_CLOUD/JUJU_LXD_CLOUD"
    )
    # Prefer a deterministic name when several clouds of the same type exist.
    for preferred in (*cloud_types, "microk8s", "localhost"):
        if preferred in matches:
            return preferred
    return sorted(matches)[0]


@pytest.fixture(scope="module")
def k8s_juju(juju_factory: pytest_jubilant.JujuFactory) -> Iterator[jubilant.Juju]:
    """Return a Juju instance for a temporary model on the K8s cloud (for cross-model tests).

    Note: unlike pytest-operator's old `ensure_model`, `get_juju` doesn't accept a
    credential name. If model creation fails due to credentials, pre-create the
    model with `juju add-model <name> <cloud> --credential <cloud>`; it will be reused.
    """
    juju = juju_factory.get_juju(suffix="k8s", cloud=_find_cloud(jubilant.Juju(), K8S_CLOUD_TYPES, K8S_CLOUD))

    # When adding a K8s cloud to a LXD controller, we need to update the
    # workload storage parameter to a storage class defined in the K8s cluster,
    # otherwise the model in unable to deploy charms. Check out these bug
    # reports for more context:
    #   - https://bugs.launchpad.net/juju/+bug/2031216
    #   - https://bugs.launchpad.net/juju/+bug/2077426
    #
    # Note: this repo's own CI (concierge-juju3.yaml) bootstraps the controller
    # on the K8s cloud and only adds LXD as a secondary cloud, so this branch is
    # a no-op there. It still matters for the common local-dev topology (an
    # LXD-hosted controller with a K8s cloud, e.g. microk8s, added to it), which
    # is why the check is dynamic rather than hardcoded.
    if _controller_cloud_type(juju) == "lxd":
        # You can get a list of available storage classes on your MicroK8s
        # cluster by:
        #   microk8s kubectl get storageclass
        juju.model_config({"workload-storage": K8S_WORKLOAD_STORAGE})
    yield juju


@pytest.fixture(scope="module")
def lxd_juju(juju_factory: pytest_jubilant.JujuFactory) -> Iterator[jubilant.Juju]:
    """Return a Juju instance for a temporary model on the LXD cloud (for cross-model tests)."""
    _ensure_lxd_cloud(jubilant.Juju())
    yield juju_factory.get_juju(suffix="lxd", cloud=_find_cloud(jubilant.Juju(), LXD_CLOUD_TYPES, LXD_CLOUD))
