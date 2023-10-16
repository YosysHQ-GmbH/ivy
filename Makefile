help:
	@echo "The following targets are available:"
	@echo
	@echo "docs:"
	@echo "  build the HTML documentation"
	@echo
	@echo "docs-{html,clean,...}:"
	@echo "  run the corresponding make target in the docs directory"
	@echo
	@echo "test:"
	@echo "  run the test suite"
	@echo
	@echo "formatting:"
	@echo "  check source code formatting"
	@echo
	@echo "reformat:"
	@echo "  reformat source code"
	@echo
	@echo "lint:"
	@echo "  check code style"
	@echo
	@echo "typecheck:"
	@echo "  run static type checker"
	@echo
	@echo "dev-install:"
	@echo "  install the package in development mode including dev dependencies"
	@echo

ifneq ($(wildcard Makefile.conf),)
include Makefile.conf
endif

O ?=

PYTHON ?= python3

SPHINXBUILD ?= $(PYTHON) -m sphinx-build

.PHONY: help docs test formatting reformat lint fix
.PHONY: typecheck ci

docs: docs-html

docs-%:
	$(MAKE) -C docs $*

test:
	$(PYTHON) -m pytest \
		--cov-report html --cov-report term \
		--cov yosys_ivy \
		-n auto -q $(O)

formatting:
	$(PYTHON) -m black --check --diff src tests $(O)

reformat:
	$(PYTHON) -m black src tests $(O)

lint:
	$(PYTHON) -m ruff check src tests $(O)

fix:
	$(PYTHON) -m ruff check --fix src tests $(O)

typecheck:
	$(PYTHON) -m pyright src tests $(O)

dev-install:
	$(PYTHON) -m pip install -e '.[dev]' $(O)

ci: formatting lint typecheck test docs-html

clean: docs-clean
	rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache htmlcov
