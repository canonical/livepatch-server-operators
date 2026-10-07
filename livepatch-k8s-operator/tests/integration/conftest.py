# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common pytest configuration and fixtures for integration tests."""

import json
import logging
import os
import pathlib
from typing import Iterator

import jubilant
import pytest
import pytest_jubilant
from helpers import K8S_CLOUD, K8S_WORKLOAD_STORAGE, LXD_CLOUD

logger = logging.getLogger(__name__)


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
    path = pathlib.Path(charm_path).resolve()
    assert path.is_file(), f"{path} is not a file"
    return path


def _controller_cloud_type(juju: jubilant.Juju) -> str:
    """Return the cloud type (e.g. "lxd", "k8s") of the controller hosting the given model."""
    model_info = json.loads(juju.cli("show-model", "--format=json"))
    controller = next(iter(model_info.values()))["controller-name"]
    controller_info = json.loads(juju.cli("show-controller", controller, "--format=json", include_model=False))
    cloud = controller_info[controller]["details"]["cloud"]
    cloud_info = json.loads(
        juju.cli("show-cloud", cloud, "--controller", controller, "--format=json", include_model=False)
    )
    if "type" not in cloud_info and cloud in cloud_info:
        cloud_info = cloud_info[cloud]
    return cloud_info.get("type", "")


@pytest.fixture(scope="module")
def k8s_juju(juju_factory: pytest_jubilant.JujuFactory) -> Iterator[jubilant.Juju]:
    """Return a Juju instance for a temporary model on the K8s cloud (for cross-model tests).

    Note: unlike pytest-operator's old `ensure_model`, `get_juju` doesn't accept a
    credential name. If model creation fails due to credentials, pre-create the
    model with `juju add-model <name> <cloud> --credential <cloud>`; it will be reused.
    """
    juju = juju_factory.get_juju(suffix="k8s", cloud=K8S_CLOUD)

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
    yield juju_factory.get_juju(suffix="lxd", cloud=LXD_CLOUD)
