# WotLK 3.3.5a custom content - task runner
#
#   make check                     run the tool selftests and the full test suite
#   make test                      unittest suite only (tools + docs integrity)
#   make selftest                  dbc_tool self-check on synthetic fixtures
#   make preflight CLIENT=dbc-client/ [SERVER=dbc-server/]
#   make manifest VERSION=2026.11.1 [STAGING=patch/]
#   make info DBC=Item.dbc
#   make clean

SHELL    := /bin/sh
PY       ?= python3
VERSION  ?= 0.0.0-dev
CLIENT   ?=
SERVER   ?=
STAGING  ?= patch
OUT      ?= manifest.json
DBC      ?= Item.dbc

.PHONY: all help check test selftest preflight manifest info clean

all: help

help:
	@echo "make check                              selftest + tests (no game files needed)"
	@echo "make preflight CLIENT=dir [SERVER=dir]  release gate for a client patch"
	@echo "make manifest VERSION=2026.11.1         stamp $(STAGING)/ -> $(OUT)"
	@echo "make info DBC=Item.dbc                  summarise a DBC file"
	@echo "make clean                              remove caches and $(OUT)"

check: selftest test

test:
	$(PY) -m unittest discover -s tests -v

selftest:
	$(PY) tools/selftest.py

preflight:
	@test -n "$(CLIENT)" || { echo "usage: make preflight CLIENT=dbc-client/ [SERVER=dbc-server/]" >&2; exit 2; }
	./tools/preflight.sh "$(CLIENT)" "$(SERVER)"

manifest:
	$(PY) tools/manifest.py make --staging "$(STAGING)" --version "$(VERSION)" --out "$(OUT)"

info:
	$(PY) tools/dbc_tool.py info "$(DBC)"

clean:
	rm -rf tools/__pycache__ tests/__pycache__ __pycache__ "$(OUT)"
