# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

ROOT := $(CURDIR)/..

##########################
# Charm related commands #
##########################
.PHONY: sync build-machine-charm deploy-machine-bundle

# Stage the shared modules and dashboards into this charm tree. Delegates to the
# root Makefile so there is a single source of truth for the copy logic.
sync:
	$(MAKE) -C $(ROOT) sync

# Builds the machine operator charm
build-machine-charm: sync
	rm -f *.charm
	CHARMCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS=true charmcraft pack --project-dir ./ --verbosity=debug

# Deploys the machine bundle using a locally build charm
deploy-machine-bundle: build-machine-charm
	juju deploy ./bundle/bundle.yaml --overlay ./bundle/machine_overlay.yaml

#####################
# INTEGRATION TESTS #
#####################
.PHONY: test-operator-machine-charm

# Runs tox integration tests against the desired target
integration-test: sync
	source venv/bin/activate && tox -e integration

# Runs tox integration tests against the desired target
unit-test: sync
	source venv/bin/activate && tox -e unit 

test: unit-test integration-test
