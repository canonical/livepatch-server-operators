# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared constants and helper functions for the machine charm's integration tests."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import jubilant
import requests
import yaml

LOGGER = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

BASE = "ubuntu@22.04"
POSTGRES_NAME = "postgresql"
HAPROXY_NAME = "haproxy"
PRO_AIRGAPPED_SERVER_NAME = "pro-airgapped-server"

DATABASE_RELATION = "database"
DATABASE_RELATION_LEGACY = "database-legacy"
WEBSITE_RELATION = "website"
PRO_AIRGAPPED_RELATION = "pro-airgapped-server"

AWAIT_POSTGRES_RELATION = "Waiting for postgres relation to be established."
SCHEMA_UPGRADE_REQUIRED = "Database not initialised, please run the schema-upgrade action."

PRO_AIRGAPPED_MANUAL_SERVER_CONFIG = (
    "QzE0TFpDQXh6MzZ3Nk5oNUVRRHVENmNtTkt0d1duOgogIGFjY291bnRJbmZvOgogICAgY3JlYXRlZEF0OiAiMjAyMi0wNS0x"
    "MlQwNjoyNzowM1oiCiAgaWQ6IGFBUFl3NzN4RwogICAgbmFtZTogYS5iQGV4YW1wbGUuY29tCiAgICB0eXBlOiBwZXJzb25h"
    "bAogIGNvbnRyYWN0SW5mbzoKICAgIGFsbG93YW5jZXM6CiAgICAtIG1ldHJpYzogdW5pdHMKICAgICAgdmFsdWU6IDMKICAg"
    "IGNyZWF0ZWRBdDogIjIwMjItMDUtMTJUMDY6Mjc6MDRaIgogICAgY3JlYXRlZEJ5OiAiIgogICAgZWZmZWN0aXZlRnJvbTog"
    "IjIwMjItMDUtMTJUMDY6Mjc6MDRaIgogICAgZWZmZWN0aXZlVG86ICI5OTk5LTEyLTMxVDAwOjAwOjAwWiIKICAgIGlkOiBj"
    "QVgtLW9OZAogICAgaXRlbXM6CiAgICAtIGNvbnRyYWN0SUQ6IGNBWC0tb05kCiAgICAgIGNyZWF0ZWQ6ICIyMDIyLTA1LTEy"
    "VDA2OjI3OjA0WiIKICAgICAgZWZmZWN0aXZlRnJvbTogIjIwMjItMDUtMTJUMDY6Mjc6MDRaIgogICAgICBlZmZlY3RpdmVU"
    "bzogIjk5OTktMTItMzFUMDA6MDA6MDBaIgogICAgICBleHRlcm5hbElEczogbnVsbAogICAgICBpZDogMzk2MjkwCiAgICAg"
    "IGxhc3RNb2RpZmllZDogIjIwMjItMDUtMTJUMDY6Mjc6MDRaIgogICAgICBtZXRyaWM6IHVuaXRzCiAgICAgIHJlYXNvbjog"
    "Y29udHJhY3RfY3JlYXRlZAogICAgICB2YWx1ZTogMwogICAgbmFtZTogYS5iQGV4YW1wbGUuY29tCiAgICBvcmlnaW46IGZy"
    "ZWUKICAgIHByb2R1Y3RzOgogICAgLSBmcmVlCiAgICByZXNvdXJjZUVudGl0bGVtZW50czoKICAgIC0gYWZmb3JkYW5jZXM6"
    "CiAgICAgICAgYXJjaGl0ZWN0dXJlczoKICAgICAgICAtIGFtZDY0CiAgICAgICAgLSB4ODZfNjQKICAgICAgICBzZXJpZXM6"
    "CiAgICAgICAgLSB4ZW5pYWwKICAgICAgICAtIGJpb25pYwogICAgICBkaXJlY3RpdmVzOgogICAgICAgIGFkZGl0aW9uYWxQ"
    "YWNrYWdlczoKICAgICAgICAtIHVidW50dS1jb21tb25jcml0ZXJpYQogICAgICAgIGFwdEtleTogOUY5MTJEQUREOTlFRTFD"
    "QzZCRkZGRjI0M0ExODZFNzMzRjQ5MUM0NgogICAgICAgIGFwdFVSTDogaHR0cHM6Ly9lc20udWJ1bnR1LmNvbS9jYwogICAg"
    "ICAgIHN1aXRlczoKICAgICAgICAtIHhlbmlhbAogICAgICAgIC0gYmlvbmljCiAgICAgIGVudGl0bGVkOiB0cnVlCiAgICAg"
    "IG9ibGlnYXRpb25zOgogICAgICAgIGVuYWJsZUJ5RGVmYXVsdDogZmFsc2UKICAgICAgdHlwZTogY2MtZWFs"
)


def unit_address(status: jubilant.Status, app: str, unit: int = 0) -> str:
    """Return the public address of a unit."""
    return status.apps[app].units[f"{app}/{unit}"].public_address


def unit_workload_message(status: jubilant.Status, app: str, unit: int = 0) -> str:
    """Return the workload status message of a unit."""
    return status.apps[app].units[f"{app}/{unit}"].workload_status.message


def http_get_json(protocol: str, address: str, port: int, path: str = "") -> dict[str, Any]:
    """Perform a GET request against a unit and parse the response body as JSON."""
    url = urljoin(f"{protocol}://{address}:{port}", path)
    LOGGER.info("GET %s", url)
    response = requests.get(url, timeout=300, verify=False)  # nosec B501 - self-signed certs in test env
    response.raise_for_status()
    return json.loads(response.content.decode())


def assert_livepatch_healthy(status: jubilant.Status, app: str = APP_NAME, unit: int = 0) -> dict[str, Any]:
    """Assert that a Livepatch unit's `debug/status` endpoint responds successfully."""
    address = unit_address(status, app, unit)
    return http_get_json("http", address, 8080, "debug/status")


def assert_haproxy_healthy(status: jubilant.Status, app: str = HAPROXY_NAME, unit: int = 0) -> dict[str, Any]:
    """Assert that a Livepatch response is reachable through an HAProxy unit."""
    address = unit_address(status, app, unit)
    return http_get_json("http", address, 80, "debug/status")


def deploy_to_active(juju: jubilant.Juju, charm: Path) -> jubilant.Status:
    """Deploy the full stack and walk the charm through its guard sequence."""
    juju.deploy(
        charm,
        app=APP_NAME,
        num_units=1,
        base=BASE,
        config={"patch-storage.type": "postgres"},
    )
    juju.deploy(POSTGRES_NAME, channel="14/stable", num_units=1, base=BASE)
    juju.deploy(HAPROXY_NAME, num_units=1, base=BASE)

    LOGGER.info("Waiting for postgresql and haproxy")
    juju.wait(lambda status: jubilant.all_active(status, POSTGRES_NAME, HAPROXY_NAME), timeout=15 * 60)

    LOGGER.info("Waiting for the charm to block on missing configuration")
    status = juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and "server.url-template" in unit_workload_message(status, APP_NAME),
        timeout=10 * 60,
    )

    haproxy_address = unit_address(status, HAPROXY_NAME)
    url_template = f"http://{haproxy_address}:80/v1/patches/{{filename}}"
    LOGGER.info("Setting server.url-template to %s", url_template)
    juju.config(APP_NAME, {"server.url-template": url_template})

    LOGGER.info("Waiting for the charm to block on the missing database relation")
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and unit_workload_message(status, APP_NAME) == AWAIT_POSTGRES_RELATION,
        timeout=5 * 60,
    )

    integrate_postgres(juju)
    integrate_haproxy(juju)

    LOGGER.info("Waiting for the charm to block on the pending schema upgrade")
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME)
        and unit_workload_message(status, APP_NAME) == SCHEMA_UPGRADE_REQUIRED,
        timeout=10 * 60,
    )

    LOGGER.info("Running the schema-upgrade action")
    task = run_schema_upgrade(juju)
    assert task.results["schema-upgrade-required"] == "False"

    return juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=5 * 60)


def scale(juju: jubilant.Juju, app: str, count: int) -> jubilant.Status:
    """Scale an application to a specific unit count, and wait for it to settle."""
    status = juju.status()
    current = len(status.apps[app].units)
    if count > current:
        juju.add_unit(app, num_units=count - current)
    elif count < current:
        units = sorted(status.apps[app].units, key=lambda name: int(name.rsplit("/", 1)[1]))[count:]
        juju.remove_unit(*units)
    else:
        return status

    return juju.wait(
        lambda status: len(status.apps[app].units) == count and jubilant.all_active(status, app),
        error=jubilant.any_error,
        timeout=20 * 60,
    )


def wait_until(condition, *, timeout: float = 300.0, delay: float = 5.0, description: str = "condition"):
    """Poll an arbitrary callable until it returns truthy, raising on timeout."""
    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            result = condition()
        except Exception as exc:  # noqa: BLE001 - retried until the deadline, then re-raised via TimeoutError
            last_error = exc
            result = None
        if result:
            return result
        time.sleep(delay)
    raise TimeoutError(f"Timed out waiting for {description}") from last_error


def integrate_postgres(juju: jubilant.Juju) -> None:
    """Integrate the Livepatch charm with the `postgresql` charm's `database` relation."""
    LOGGER.info("Integrating %s and %s", APP_NAME, POSTGRES_NAME)
    juju.integrate(f"{APP_NAME}:{DATABASE_RELATION}", f"{POSTGRES_NAME}:{DATABASE_RELATION}")
    juju.wait(
        lambda status: unit_workload_message(status, APP_NAME) != AWAIT_POSTGRES_RELATION,
        error=jubilant.any_error,
        timeout=15 * 60,
    )


def integrate_haproxy(juju: jubilant.Juju) -> None:
    """Integrate the Livepatch charm with the `haproxy` charm's `website` relation."""
    LOGGER.info("Integrating %s and %s", APP_NAME, HAPROXY_NAME)
    juju.integrate(f"{APP_NAME}:{WEBSITE_RELATION}", HAPROXY_NAME)


def run_schema_upgrade(juju: jubilant.Juju, unit: str = f"{APP_NAME}/0") -> jubilant.Task:
    """Run the `schema-upgrade` action and wait for the charm to settle."""
    task = juju.run(unit, "schema-upgrade")
    juju.wait(lambda status: jubilant.all_active(status, APP_NAME), error=jubilant.any_error, timeout=10 * 60)
    return task
