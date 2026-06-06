.ONESHELL:
SHELL      := $(shell which bash)
.SHELLFLAGS := -ec

VERSION ?=
DRY_RUN ?= false

.PHONY: release

# https://stackoverflow.com/a/10858332
# Check that given variables are set and all have non-empty values,
# die with an error otherwise.
#
# Params:
#   1. Variable name(s) to test.
#   2. (optional) Error message to print.
check_defined = \
    $(strip $(foreach 1,$1, \
        $(call __check_defined,$1,$(strip $(value 2)))))
__check_defined = \
    $(if $(value $1),, \
      $(error Undefined $1$(if $2, ($2))))

release:
	@:$(call check_defined, VERSION, version bump type - '(major|minor|patch)')
	if [[ ! "$(VERSION)" =~ ^(major|minor|patch)$$ ]]; then
	echo "Invalid version bump: $(VERSION). Expected one of: major, minor, patch"
		exit 1
	fi
	if [[ $(DRY_RUN) ]]; then
		gh workflow run release.yml -f bump=$(VERSION) -f dry_run=true
	else
		gh workflow run release.yml -f bump=$(VERSION)
	fi
	sleep 1
	gh run watch $(gh run list --workflow=release.yml --json databaseId | jq -r 'first | .databaseId')
