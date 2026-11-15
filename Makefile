.DEFAULT_GOAL := help

PYTHON ?= python3
ARGS ?=

.PHONY: help run once json locks collect snapshot install uninstall test

help:
	@printf '%s\n' \
	  'Logged time calculator' \
	  '' \
	  '  make run        Show the live daily report (Ctrl-C to exit)' \
	  '  make once       Print one daily JSON report' \
	  '  make json       Print JSON reports every refresh' \
	  '  make locks      List today’s lock periods and total locked time' \
	  '  make collect    Collect lock history in the foreground' \
	  '  make snapshot   Save current lock history once' \
	  '  make install    Install logged-time on PATH and enable login collection' \
	  '  make uninstall  Remove command and autostart; preserve saved history' \
	  '  make test       Run the offline test suite' \
	  '' \
	  'Options: PYTHON=python3 ARGS="--refresh 30"' \
	  'Example: make once ARGS="--date 2026-11-14"' \
	  'See README.md for login, shared-home storage, and accuracy details.'

run:
	$(PYTHON) daily.py $(ARGS)

once:
	$(PYTHON) daily.py --once $(ARGS)

json:
	$(PYTHON) daily.py --json $(ARGS)

locks:
	$(PYTHON) shared_history.py list $(ARGS)

collect:
	$(PYTHON) shared_history.py collect $(ARGS)

snapshot:
	$(PYTHON) shared_history.py collect --once $(ARGS)

install:
	bash ./install.sh

uninstall:
	bash ./install.sh --uninstall

test:
	$(PYTHON) -m unittest -v
