# Copy the shared modules and dashboards into the charm trees before packing
# or running tests. Charmcraft builds operate inside the charm directory and
# cannot reference paths outside it, so the shared sources are staged here.

SHARED := $(CURDIR)/shared
K8S := livepatch-k8s-operator
MACHINE := livepatch-machine-operator

.PHONY: sync pack opcli-build opcli-test opcli-test-k8s opcli-test-machine opcli-spread

CONCIERGE := concierge-juju3.yaml

sync:
	@test -n "$$(ls -A $(SHARED)/livepatch_common)" || (echo "error: empty $(SHARED)/livepatch_common" && exit 1)
	@mkdir -p $(K8S)/src $(K8S)/cos_custom/grafana_dashboards $(MACHINE)/src/grafana_dashboards
	@rm -rf $(K8S)/src/livepatch_common $(MACHINE)/src/livepatch_common
	@cp -r $(SHARED)/livepatch_common $(K8S)/src/livepatch_common
	@cp -r $(SHARED)/livepatch_common $(MACHINE)/src/livepatch_common
	@cp $(SHARED)/grafana_dashboards/*.json $(K8S)/cos_custom/grafana_dashboards/
	@cp $(SHARED)/grafana_dashboards/*.json $(MACHINE)/src/grafana_dashboards/

pack: sync
	cd $(K8S) && charmcraft pack
	cd $(MACHINE) && charmcraft pack

opcli-build: sync
	opcli artifacts build

opcli-spread: opcli-build
	opcli spread run

opcli-test: opcli-build
	opcli env provision -c $(CONCIERGE)
	opcli artifacts push-images --missing-registry deploy
	opcli pytest run --suite $(K8S)/tests/integration/
	opcli pytest run --suite $(MACHINE)/tests/integration/

opcli-test-k8s: opcli-build
	opcli env provision -c $(CONCIERGE)
	opcli artifacts push-images --missing-registry deploy
	opcli pytest run --suite $(K8S)/tests/integration/

opcli-test-machine: opcli-build
	opcli env provision -c $(CONCIERGE)
	opcli pytest run --suite $(MACHINE)/tests/integration/