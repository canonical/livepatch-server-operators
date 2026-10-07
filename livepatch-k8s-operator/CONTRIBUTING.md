# Contributing

## Overview

This documents explains the processes and practices recommended for contributing enhancements to this operator.

- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `develop` branch. 
This also avoids merge commits and creates a linear Git commit history.

## Developing

You can create an environment for development with `tox`:

```shell
tox -e integration
source venv/bin/activate
```

### Testing

```shell
tox -e fmt                    # update your code according to linting rules
tox -e lint                   # code style
tox -e unit                   # unit tests
tox -e integration            # integration tests
tox -e integration-ingress    # integration tests for ingress
tox -e integration-metricsdb  # integration tests for the metrics (TimescaleDB) database
tox -e integration-airgapped  # integration tests for air-gapped deployments
tox                           # runs 'lint' and 'unit' environments
```

### Integration tests

The integration tests use [Jubilant](https://documentation.ubuntu.com/jubilant/) and
[pytest-jubilant](https://github.com/canonical/pytest-jubilant). They need a Juju 3.x
controller, and they don't pack the charm, so pack it first:

```shell
CHARMCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS=1 charmcraft pack
LIVEPATCH_APP_IMAGE=ghcr.io/canonical/livepatch-server:<tag> tox -e integration
```

Environment variables:

- `LIVEPATCH_APP_IMAGE` (required): OCI image (rock) used for the charm's `app-image` resource.
  Its `vX.Y.Z` tag is also the expected charm version.
- `CHARM_PATH` (optional): path to the packed charm. Defaults to the single `*.charm` file in the charm root.
- `JUJU_K8S_CLOUD` / `JUJU_LXD_CLOUD` (optional, cross-model tests): cloud names, default `microk8s` / `localhost`.
- `JUJU_K8S_WORKLOAD_STORAGE` (optional): K8s storage class set on the K8s model when the controller runs on LXD,
  default `microk8s-hostpath`.

Useful pytest-jubilant options (pass them after `--`, e.g. `tox -e integration -- --no-juju-teardown`):

- `--no-juju-teardown`: keep the models (and skip `juju_teardown` tests) for inspection.
- `--juju-model <prefix>`: use a fixed model-name prefix instead of a random one.
- `--no-juju-setup`: skip `juju_setup` tests and reuse existing models (requires `--juju-model`).


## Build charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charm
juju deploy ./canonical-livepatch-server-k8s_amd64.charm
```
