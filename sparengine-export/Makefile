# Sparengine — QA / test entry points.
#
# Run from the sparengine-export/ directory inside the running container:
#     docker compose exec sparengine bash -c "cd /app/sparengine-export && make <target>"
#
# OR (host-side wrapper) from the repo root:
#     docker compose exec sparengine make -C /app/sparengine-export <target>
#
# Targets:
#     test            — full test pyramid: unit + lint + integration
#     test-fast       — pre-commit tier: unit + lint only (no Neo4j)
#     test-unit       — unit tests only
#     test-lint       — lint tests only
#     test-integration — integration tests only (requires Neo4j up)
#     test-regression — slow per-archetype regression
#     scorecard ASSET=<workdir> — Layer B mechanical rubric on one workdir
#     llm-judge ASSET=<workdir> — Layer B LLM-as-judge sampling
#     archive   ARCHETYPE=<name> WORKDIR=<path> VERSION=<v> — Layer C snapshot
#     analyse                — Layer C: agent-driven verdict on most-recent two
#     analyse-phase PHASE=<id> — same, scoped to one phase
#     compare FROM=<v> TO=<v> — Layer C side-by-side diff (no agent)
#     update-baseline ARCHETYPE=<name> — promote current run to baseline
#     benchmark-list         — show all archived versions

PYTHON   ?= python
PYTEST   ?= $(PYTHON) -m pytest
TOOLS    ?= $(PYTHON) -m

.PHONY: help test test-fast test-unit test-lint test-integration test-regression \
        scorecard llm-judge archive analyse analyse-phase compare \
        update-baseline benchmark-list

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | head -25

# ---- Test pyramid ---------------------------------------------------------

test:
	$(PYTEST) tests/ -v

test-fast:
	$(PYTEST) tests/ -m "unit or lint" -q

test-unit:
	$(PYTEST) tests/ -m unit -q

test-lint:
	$(PYTEST) tests/ -m lint -q

test-integration:
	$(PYTEST) tests/ -m integration -v

test-regression:
	$(PYTEST) tests/ -m regression -v

# ---- Layer B (per-real-asset) --------------------------------------------

scorecard:
	@if [ -z "$(ASSET)" ]; then echo "usage: make scorecard ASSET=/path/to/workdir"; exit 2; fi
	$(TOOLS) tools.quality_scorecard --workdir $(ASSET)

llm-judge:
	@if [ -z "$(ASSET)" ]; then echo "usage: make llm-judge ASSET=/path/to/workdir [SAMPLE=10]"; exit 2; fi
	$(TOOLS) tools.llm_judge --workdir $(ASSET) --sample $(or $(SAMPLE),10)

llm-judge-skip:
	@if [ -z "$(ASSET)" ]; then echo "usage: make llm-judge-skip ASSET=/path/to/workdir"; exit 2; fi
	$(TOOLS) tools.llm_judge --workdir $(ASSET) --no-llm

# ---- Layer C (cross-version) ---------------------------------------------

archive:
	@if [ -z "$(ARCHETYPE)" ] || [ -z "$(WORKDIR)" ] || [ -z "$(VERSION)" ]; then \
		echo "usage: make archive ARCHETYPE=helicopter_full WORKDIR=/path VERSION=v4-..."; exit 2; \
	fi
	$(TOOLS) tools.benchmark_archive --archetype $(ARCHETYPE) --workdir $(WORKDIR) --version $(VERSION)

analyse:
	$(TOOLS) tools.analyse_change --previous

analyse-phase:
	@if [ -z "$(PHASE)" ]; then echo "usage: make analyse-phase PHASE=phase4_components"; exit 2; fi
	$(TOOLS) tools.analyse_change --previous --phase $(PHASE)

compare:
	@if [ -z "$(FROM)" ] || [ -z "$(TO)" ]; then echo "usage: make compare FROM=v3 TO=v4 [PHASE=phase4_components]"; exit 2; fi
	$(TOOLS) tools.compare_versions $(FROM) $(TO) $(if $(PHASE),--phase $(PHASE))

update-baseline:
	@echo "Baseline lives in benchmarks/ — to make a version the new baseline,"
	@echo "  manually re-tag it (e.g. add a 'baseline' marker into index.json)."
	@echo "  Automated promotion is intentionally manual: a human should review"
	@echo "  the verdict.md before locking in new counts."

benchmark-list:
	@$(PYTHON) -c "import json, pathlib, sys; \
ix = pathlib.Path('benchmarks/index.json'); \
print('no archive yet') if not ix.is_file() else \
[print(v) for v in sorted(json.loads(ix.read_text()).get('versions', {}).keys())]"
