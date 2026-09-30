# Guidance for coding and review agents

## Repository layout

- `livepatch-k8s-operator/`: Kubernetes Juju charm.
- `livepatch-machine-operator/`: machine Juju charm.
- `shared/livepatch_common/`: shared Python package used by both charms.
- `shared/grafana_dashboards/`: dashboards shared by both charms.
- `shared/tests/`: tests for shared code.

Charmcraft packages only files inside each charm directory. The generated synced
copies are:

- `livepatch-k8s-operator/src/livepatch_common/`
- `livepatch-machine-operator/src/livepatch_common/`
- `livepatch-k8s-operator/cos_custom/grafana_dashboards/`
- `livepatch-machine-operator/src/grafana_dashboards/`

Do not edit those synced copies directly. Edit `shared/`, then run `make sync`.

## Build and test workflow

- Run `make sync` after changing anything in `shared/`.
- Run `make sync` before packing, per-charm unit tests, or linting.
- Preferred build: `make opcli-build`.
- Alternative build: `make pack`.
- Root tests: `tox -e shared`.
- Per-charm tests, after `make sync`:
  - `cd livepatch-k8s-operator && tox -e unit`
  - `cd livepatch-machine-operator && tox -e unit`

The two charms must expose identical config options. Shared config handling
(the secret-option mappings and their resolution) lives in
`shared/livepatch_common/config.py`; keep both charms' config option
definitions in sync when changing charm config.

## Packaging guardrails

- There must never be a root-level `lib/` directory.
- Do not add `pack-dir` to `artifacts.yaml`; `opcli` must use each charm's
  `charmcraft-yaml` path.
- `.github/workflows/integration_test.yaml` is a hand-rolled copy of
  canonical/charm-ci's `build-artifacts.yml` + `integration-test.yml`, because
  those reusable workflows cannot run `make sync` before packing charms. Keep
  its job and artifact names identical to charm-ci's.

## Review guidance

- Do not review, lint, or modify `livepatch-k8s-operator/lib/` or
  `livepatch-machine-operator/lib/`. These are vendored charm libraries fetched
  by charmcraft, not project code, and are not a style reference.
- Ignore diffs in synced copies listed above; review the corresponding source in
  `shared/` instead.
