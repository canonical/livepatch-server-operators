---
name: reviewing-livepatch-server-operators
description: Review pull requests and diffs in the livepatch-server-operators monorepo (k8s + machine Juju charms with a shared/ package). Use when asked to review changes, a PR, or a branch in this repository.
---

# Reviewing livepatch-server-operators changes

## Scope: what to review and what to skip

Skip these entirely. Do not review, lint, comment on, or suggest changes to:

- `livepatch-k8s-operator/lib/` and `livepatch-machine-operator/lib/`: vendored
  charm libraries fetched by charmcraft (`charm-libs` in `charmcraft.yaml`).
  They are not project code and are not a style reference.
- Generated synced copies of `shared/`, produced by `make sync` and gitignored:
  - `livepatch-k8s-operator/src/livepatch_common/`
  - `livepatch-machine-operator/src/livepatch_common/`
  - `livepatch-k8s-operator/cos_custom/grafana_dashboards/`
  - `livepatch-machine-operator/src/grafana_dashboards/`

Review the sources instead:

- `shared/livepatch_common/`: shared Python package used by both charms.
- `shared/grafana_dashboards/`: dashboards shared by both charms.
- `shared/tests/`: tests for the shared package.
- Each charm's own `src/`, `tests/`, `charmcraft.yaml` / `config.yaml`, and
  root CI/build files.

## Things to flag

- **Edits to synced copies.** Any tracked change under the synced paths above
  is a bug: the change belongs in `shared/`, followed by `make sync`. Synced
  copies and `__pycache__`/`*.pyc` files must never be committed.
- **Logic duplicated across charms.** Code that both charms need belongs in
  `shared/livepatch_common/`, not copy-pasted into each charm's `src/`.
- **Charm-specific code in `shared/`.** Shared modules must not import `ops`
  relation objects or charm-only modules. Charm wrappers (e.g. each charm's
  `src/log_redactor.py`) register charm-specific behaviour on top.
- **Config parity.** Both charms must expose identical config options (names,
  types, defaults and descriptions, except the intentional differences listed
  in `tests/config_parity_lib.py`). Config changes to one charm without the
  matching change to the other should fail `tox -e config-parity`.
- **Sensitive config.** A new secret-bearing option must be added to the log
  redaction patterns: the `LP_*` env var in `shared/livepatch_common/log_redactor.py`
  and, for the machine charm, the `lp.*` snap key in its `src/log_redactor.py`.
- **Packaging guardrails.**
  - There must never be a root-level `lib/` directory.
  - `artifacts.yaml` must not set `pack-dir`; opcli resolves it from the repo
    root, which packs from the root and downloads charm libs into `<root>/lib`.
  - Each packed charm must contain only its own files (the k8s charm must not
    ship machine-charm code and vice versa).
- **CI.** `.github/workflows/integration_test.yaml` is a hand-rolled copy of
  canonical/charm-ci's `build-artifacts.yml` + `integration-test.yml`, because
  those reusable workflows cannot run `make sync` before packing. Its job names
  (`Build {type} {name} ({arch})`) and artifact names (`built-*`,
  `artifacts-build-*`) must stay identical to charm-ci's, since
  `opcli artifacts fetch` and the build-failure check depend on them. Any
  workflow that builds or unit-tests a charm must run `make sync` first.

## Verifying a change

Run `make sync` before any per-charm command.

- `tox -e shared`: shared package tests.
- `tox -e config-parity`: config parity between the charms.
- `cd livepatch-machine-operator && tox -e unit`
- `cd livepatch-k8s-operator && tox -e unit`
- `make opcli-build` (preferred) or `make pack`: builds both charms. Afterwards
  check that no root `lib/` exists and inspect contents with `unzip -Z1 <charm>`.
