# Canonical Livepatch Server Operators

## Description

This repository contains the [Canonical Livepatch Server](https://ubuntu.com/security/livepatch)
Juju charms for Kubernetes and machines. Livepatch Server serves kernel patches
and their metadata to clients, allowing administrators to control patch rollout
across their infrastructure.

- [Kubernetes charm](livepatch-k8s-operator/README.md):
  [`canonical-livepatch-server-k8s`](https://charmhub.io/canonical-livepatch-server-k8s)
- [Machine charm](livepatch-machine-operator/README.md):
  [`canonical-livepatch-server`](https://charmhub.io/canonical-livepatch-server)
  <!-- TODO: Update the machine charm URL when its new Charmhub page is available. -->

For an on-prem deployment including the server's dependencies, use the
[Livepatch on-prem bundle](https://charmhub.io/canonical-livepatch-onprem)
instead of deploying a server charm on its own.

## Usage

Deploy the charm appropriate for your Juju model:

```sh
# In a Kubernetes model
juju deploy canonical-livepatch-server-k8s
```

```sh
# In a machine model
juju deploy canonical-livepatch-server-vm
```

### Database integration

The server requires PostgreSQL. The Kubernetes and machine charms use
different endpoint names:

```sh
# In a Kubernetes model
juju deploy postgresql-k8s --trust
juju integrate canonical-livepatch-server-k8s:postgresql postgresql-k8s:database
```

```sh
# In a machine model
juju deploy postgresql
juju integrate canonical-livepatch-server-vm:database postgresql:database
```

See the [Kubernetes](livepatch-k8s-operator/README.md#integrations) and
[machine](livepatch-machine-operator/README.md#integrations) charm READMEs for
other integrations.

## Development

The charms share Python modules in `shared/livepatch_common/` and Grafana
dashboards in `shared/grafana_dashboards/`. Charmcraft builds within each charm
directory, so these sources must be staged inside the charm trees before
packing or running per-charm tests:

```sh
make sync
```

The copies in `livepatch-k8s-operator/src/livepatch_common/`,
`livepatch-machine-operator/src/livepatch_common/`, and the charms' dashboard
directories are generated. Edit `shared/`, not the copies, and rerun
`make sync` after changes.

### Building the charms

Build both charms with `opcli` (preferred), or pack them with Charmcraft:

```sh
make opcli-build
# Or:
make pack
```

Both targets run `make sync` automatically. Every Make target that packs or
tests a charm now runs `make sync` first — including the per-charm targets
(`make operator-prod-k8s` for Kubernetes, `make build-machine-charm`,
`make unit-test`, and `make integration-test` for machine) — so the staged
copies can't go stale. You only need to run `make sync` by hand if you invoke
`charmcraft pack` or `tox` directly instead of through a Make target. The
artifacts are:

- `livepatch-k8s-operator/canonical-livepatch-server-k8s_amd64.charm`
- `livepatch-machine-operator/canonical-livepatch-server_ubuntu@22.04-amd64.charm`

### Running tests

Run every unit test suite from the repository root:

```sh
make unit-test
```

That runs the shared-module tests plus each charm's unit tests, syncing the
shared sources into the charm trees first. To run a single suite:

```sh
make unit-test-shared    # shared/tests against shared/livepatch_common
make unit-test-k8s       # livepatch-k8s-operator
make unit-test-machine   # livepatch-machine-operator
```
## License

These charms are distributed under the Apache License, Version 2.0.
See [LICENSE](LICENSE) for details.
