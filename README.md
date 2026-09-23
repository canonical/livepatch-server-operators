# livepatch-server-operators

Monorepo for the [Livepatch Server](https://ubuntu.com/security/livepatch)
## Building the charms
### 1. Stage the shared sources

Charmcraft builds operate inside each charm's directory and cannot reference
files outside it, so the shared code must be copied into the charm trees first:

```sh
make sync
```

This copies `shared/livepatch_common/` into both charms' `src/` directories and
the shared dashboards into both charms' dashboard directories. Run it again
after any change to `shared/`.

### 2. Build the charms

Build both charms:

```sh
make pack
```

Or build each individually:

```sh
cd livepatch-k8s-operator && charmcraft pack
cd livepatch-machine-operator && charmcraft pack
```

Each command produces a `.charm` file in the charm's directory, named after the
charm and its base/architecture, e.g.:

- `livepatch-k8s-operator/canonical-livepatch-server-k8s_amd64.charm`
- `livepatch-machine-operator/canonical-livepatch-server_ubuntu-22.04-amd64.charm`

You can then deploy the local artifact with:

```sh
juju deploy ./livepatch-k8s-operator/canonical-livepatch-server-k8s_*.charm
juju deploy ./livepatch-machine-operator/canonical-livepatch-server_*.charm
```

## Running tests

```sh
# shared/livepatch_common unit tests
tox -e shared

# machine charm unit tests
cd livepatch-machine-operator && tox -e unit

# k8s charm unit tests
cd livepatch-k8s-operator && tox -e unit

# config-option parity between the two charms
tox -e config-parity
```

The `*.charm` files and the `.tox` environments are gitignored.

## Making changes to shared code

1. Edit the files under `shared/livepatch_common/` (and `shared/grafana_dashboards/` if needed).
2. Run `make sync` to restage them into both charm trees.
3. `make pack` to rebuild, or run the `tox` environments above to test.
