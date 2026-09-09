# Lernapp — developer shortcuts. Installers for end users: see packaging/README.md
.PHONY: help bootstrap dev test lint pkg-macos pkg-linux pkg-windows-zip release-local verify-installers clean

PYTHON ?= $(if $(wildcard .venv/bin/python),$(CURDIR)/.venv/bin/python,python3)
export PYTHON

VERSION := $(shell sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -1)

help:
	@echo "make bootstrap        dev setup (uv sync incl. dev, STT model, doctor)"
	@echo "make dev              API --reload + Streamlit (uv run lernapp start --dev)"
	@echo "make test             pytest -q"
	@echo "make lint             ruff check + mypy --strict"
	@echo "make pkg-macos        dist/Lernapp-$(VERSION)-macos.pkg (macOS only)"
	@echo "make pkg-linux        dist/lernapp-$(VERSION)-linux-installer.sh"
	@echo "make pkg-windows-zip  dist/lernapp-$(VERSION)-windows.zip (Setup.exe only in CI)"
	@echo "make release-local    all of the above that this OS can build"

bootstrap:
	scripts/bootstrap.sh

dev:
	scripts/run_dev.sh

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run mypy

pkg-macos:
	bash packaging/macos/build_pkg.sh

pkg-linux:
	bash packaging/linux/build_installer.sh

pkg-windows-zip:
	bash packaging/windows/build_zip.sh

release-local: pkg-linux pkg-windows-zip
	"$(PYTHON)" packaging/common/build_update.py
	@if [ "$$(uname -s)" = Darwin ]; then bash packaging/macos/build_pkg.sh; else echo "skip macOS pkg (needs macOS)"; fi
	@$(MAKE) verify-installers

verify-installers:
	"$(PYTHON)" packaging/common/verify_artifacts.py dist/lernapp-$(VERSION)-linux-installer.sh dist/lernapp-$(VERSION)-windows.zip
	@if [ "$$(uname -s)" = Darwin ]; then "$(PYTHON)" packaging/common/verify_artifacts.py dist/Lernapp-$(VERSION)-macos.pkg; fi

clean:
	rm -rf build dist
