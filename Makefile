# Copy the shared modules and dashboards into the charm trees before packing
# or running tests. Charmcraft builds operate inside the charm directory and
# cannot reference paths outside it, so the shared sources are staged here.

SHARED := $(CURDIR)/shared
K8S := livepatch-k8s-operator
MACHINE := livepatch-machine-operator
MACHINE_BASES := 20.04 22.04 24.04 26.04

.PHONY: sync pack unit-test unit-test-shared unit-test-k8s unit-test-machine \
	opcli-build opcli-test opcli-test-k8s opcli-test-machine opcli-run-machine-suites opcli-spread

CONCIERGE := concierge-juju3.yaml
CONCIERGE_MACHINE := concierge-machine-juju3.yaml

sync:
	@test -n "$$(ls -A $(SHARED)/livepatch_common)" || (echo "error: empty $(SHARED)/livepatch_common" && exit 1)
	@rm -rf $(K8S)/src/livepatch_common $(MACHINE)/src/livepatch_common
	@cp -r $(SHARED)/livepatch_common $(K8S)/src/livepatch_common
	@cp -r $(SHARED)/livepatch_common $(MACHINE)/src/livepatch_common
	@rm -rf $(K8S)/cos_custom/grafana_dashboards $(MACHINE)/src/grafana_dashboards
	@mkdir -p $(K8S)/src $(K8S)/cos_custom/grafana_dashboards $(MACHINE)/src/grafana_dashboards
	@cp $(SHARED)/grafana_dashboards/*.json $(K8S)/cos_custom/grafana_dashboards/
	@cp $(SHARED)/grafana_dashboards/*.json $(MACHINE)/src/grafana_dashboards/

pack: sync
	cd $(K8S) && CHARMCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS=true charmcraft pack
	cd $(MACHINE) && CHARMCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS=true charmcraft pack

# Unit tests. Each per-charm target re-runs sync so the staged copies of
# shared/livepatch_common are current in the charm tree it tests.
unit-test: sync
	$(MAKE) unit-test-shared
	$(MAKE) unit-test-k8s
	$(MAKE) unit-test-machine

# Tests shared/tests against shared/livepatch_common directly. These sources are
# not staged into the charm trees, so no sync is needed here.
unit-test-shared:
	tox -e shared

unit-test-k8s: sync
	cd $(K8S) && tox -e unit

unit-test-machine: sync
	cd $(MACHINE) && tox -e unit

opcli-build: sync
	opcli artifacts build

opcli-spread: opcli-build
	opcli spread run

opcli-test: opcli-build
	opcli env provision -c $(CONCIERGE)
	opcli artifacts push-images --missing-registry deploy
	opcli pytest run --suite $(K8S)/tests/integration/
	$(MAKE) opcli-run-machine-suites

opcli-test-k8s: opcli-build
	opcli env provision -c $(CONCIERGE)
	opcli artifacts push-images --missing-registry deploy
	opcli pytest run --suite $(K8S)/tests/integration/

opcli-test-machine: opcli-build
	opcli env provision -c $(CONCIERGE_MACHINE)
	$(MAKE) opcli-run-machine-suites

# One suite per base: CHARM_BASE selects the matching build, as in spread.yaml.
# The suite keys are labels (discovery uses the shared discover-path), so each
# base suite runs the full integration set against its own build.
opcli-run-machine-suites:
	for base in $(MACHINE_BASES); do \
		CHARM_BASE=ubuntu@$$base opcli pytest run --suite $(MACHINE)/tests/integration/ubuntu-$$base/ || exit 1; \
	done
