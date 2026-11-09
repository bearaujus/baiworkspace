.DEFAULT_GOAL := help
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3
else
PYTHON ?= python3
endif
PROFILE ?= dev
export TOOL PROFILE KEY FILE OUT QUERY REPO

.PHONY: help init doctor status context secrets-setup secrets-list secret-set secret-remove backup restore-backup test
help init doctor status context secrets-setup secrets-list secret-set secret-remove backup restore-backup:
	@$(PYTHON) scripts/make_target.py $@

test:
	@$(PYTHON) -B -m unittest discover -s tests -v
