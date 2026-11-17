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

.PHONY: all help check test test-intern test-engineer test-master selftest preflight manifest catalog info clean

all: help

help:
	@echo "make check                              selftest + all three test tiers"
	@echo "make test-intern|test-engineer|test-master   one tier at a time"
	@echo "make preflight CLIENT=dir [SERVER=dir]  release gate for a client patch"
	@echo "make manifest VERSION=2026.11.1         stamp $(STAGING)/ -> $(OUT)"
	@echo "make catalog                            what can be altered, and what players see"
	@echo "make info DBC=Item.dbc                  summarise a DBC file"
	@echo "make clean                              remove caches and $(OUT)"

check: selftest test

test:
	$(PY) -m unittest discover -s tests -v

test-intern:
	$(PY) -m unittest tests.test_tier_intern -v

test-engineer:
	$(PY) -m unittest tests.test_tier_engineer -v

test-master:
	$(PY) -m unittest tests.test_tier_master -v

selftest:
	$(PY) tools/selftest.py

preflight:
	@test -n "$(CLIENT)" || { echo "usage: make preflight CLIENT=dbc-client/ [SERVER=dbc-server/]" >&2; exit 2; }
	./tools/preflight.sh "$(CLIENT)" "$(SERVER)"

manifest:
	$(PY) tools/manifest.py make --staging "$(STAGING)" --version "$(VERSION)" --out "$(OUT)"

catalog:
	$(PY) tools/catalog.py stats
	@echo ""
	@$(PY) tools/catalog.py matrix

info:
	$(PY) tools/dbc_tool.py info "$(DBC)"

clean:
	rm -rf tools/__pycache__ tests/__pycache__ __pycache__ "$(OUT)"
