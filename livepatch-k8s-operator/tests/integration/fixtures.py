# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Deployment helpers shared by the integration test modules."""

import logging
import pathlib
from typing import Optional

import jubilant
from helpers import (
    ACTIVE_STATUS,
    APP_IMAGE,
    APP_IMAGE_RESOURCE,
    APP_NAME,
    APP_PORT,
    BLOCKED_STATUS,
    DB_ENDPOINT,
    LEGACY_DB_ENDPOINT,
    POSTGRESQL_CHANNEL,
    POSTGRESQL_NAME,
    fast_forward,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)

JAMMY = "ubuntu@22.04"

CHARM_CONFIG = {
    "patch-storage.type": "postgres",
    "auth.basic.enabled": True,
    "contracts.enabled": False,
    "patch-cache.cache-size": 128,
    "patch-cache.cache-ttl": "1h",
    "patch-cache.enabled": True,
    "patch-sync.enabled": True,
    "server.burst-limit": 500,
    "server.concurrency-limit": 50,
    "server.is-hosted": True,
    "server.log-level": "info",
}


def deploy_package(
    juju: jubilant.Juju,
    charm: Optional[pathlib.Path] = None,
    use_current_stable: bool = False,
) -> None:
    """Deploy the application and its dependencies.

    Args:
        juju: Juju instance operating on the target (K8s) model.
        charm: Path of the local charm; required unless `use_current_stable`.
        use_current_stable: if True, the current latest stable release of the charm is
            deployed (instead of the local charm).
    """
    if use_current_stable:
        logger.info("Deploying current stable release")
        juju.deploy(APP_NAME, APP_NAME, config=CHARM_CONFIG, num_units=1, base=JAMMY)
        db_endpoint = LEGACY_DB_ENDPOINT
    else:
        assert charm is not None, "charm is required for local deploys"
        assert APP_IMAGE, f"no OCI image for the {APP_IMAGE_RESOURCE!r} resource; set LIVEPATCH_APP_IMAGE"
        logger.info("Deploying local charm %s with %s=%s", charm, APP_IMAGE_RESOURCE, APP_IMAGE)
        # Juju rejects a local charm deploy on a k8s model unless every
        # oci-image resource is supplied explicitly; it does not fall back to
        # the `upstream-source` declared in charmcraft.yaml (that default only
        # applies when charmcraft uploads the charm to Charmhub).
        juju.deploy(
            charm,
            APP_NAME,
            config=CHARM_CONFIG,
            num_units=1,
            resources={APP_IMAGE_RESOURCE: APP_IMAGE},
        )
        db_endpoint = DB_ENDPOINT

    juju.deploy(POSTGRESQL_NAME, POSTGRESQL_NAME, base=JAMMY, channel=POSTGRESQL_CHANNEL, trust=True)

    with fast_forward(juju):
        logger.info(f"Waiting for {POSTGRESQL_NAME}")
        wait_for_apps(juju, [POSTGRESQL_NAME], status=ACTIVE_STATUS, timeout=600)
        logger.info(f"Waiting for {APP_NAME}")
        wait_for_apps(juju, [APP_NAME], status=BLOCKED_STATUS, timeout=600)

        logger.info("Making relations")
        perform_livepatch_integrations(juju, db_endpoint)
        wait_for_apps(juju, [APP_NAME], status=BLOCKED_STATUS, timeout=600)

        logger.info("Setting server.url-template")
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=APP_PORT)
        url_template = f"{url}/v1/patches/{{filename}}"
        logger.info(f"Set server.url-template to {url_template}")
        juju.config(APP_NAME, {"server.url-template": url_template})

        # The charm automatically triggers schema upgrade after the integrations are made.
        # So, we should wait for an active state.
        logger.info("Waiting for active idle")
        status = wait_for_apps(juju, [APP_NAME], status=ACTIVE_STATUS, timeout=300)
        assert status.apps[APP_NAME].units[f"{APP_NAME}/0"].workload_status.current == ACTIVE_STATUS


def deploy_package_if_needed(
    juju: jubilant.Juju,
    charm: Optional[pathlib.Path] = None,
    use_current_stable: bool = False,
) -> None:
    """Deploy the application and its dependencies only if not already deployed."""
    if APP_NAME in juju.status().apps:
        logger.info("%s already deployed; skipping deploy", APP_NAME)
        return

    deploy_package(juju, charm=charm, use_current_stable=use_current_stable)


def perform_livepatch_integrations(juju: jubilant.Juju, db_endpoint: str = DB_ENDPOINT) -> None:
    """Integrate the Livepatch charm with postgresql-k8s.

    Args:
        juju: Juju instance operating on the target model.
        db_endpoint: Livepatch's database endpoint name.
    """
    logger.info("Integrating Livepatch and Postgresql")
    juju.integrate(f"{APP_NAME}:{db_endpoint}", f"{POSTGRESQL_NAME}:database")
