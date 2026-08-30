# Local (single-binary) and containerised (multi-zone) builds.
CXX      ?= g++
CXXFLAGS ?= -std=c++20 -O2 -pthread -Iinclude
COMPOSE  := docker compose -f infra/docker-compose.yml

.PHONY: all local dispatcher adapter scenario hsms-sim test bench run api ui infra-up infra-down verify reach clean

all: local
local: dispatcher adapter scenario hsms-sim

dispatcher:
	$(CXX) $(CXXFLAGS) src/e2e_main.cpp -o fabdisp

scenario:
	$(CXX) $(CXXFLAGS) src/scenario_main.cpp -o fab_scenario

hsms-sim:
	$(CXX) $(CXXFLAGS) src/hsms_sim_main.cpp -o hsms_sim

test:
	$(CXX) $(CXXFLAGS) src/test_main.cpp -o fabtest
	./fabtest

bench:
	$(CXX) $(CXXFLAGS) src/test_main.cpp -o fabtest
	./fabtest --bench 2

# Two processes, real TCP, real HSMS handshake. Proves the transport path.
hsms-test: hsms-sim adapter
	@./hsms_sim --listen 127.0.0.1:5000 --event-hz 4 & sim=$$!; sleep 1; \
	 timeout 12 ./amhs_adapter --hsms=127.0.0.1:5000; \
	 kill $$sim 2>/dev/null || true; wait $$sim 2>/dev/null || true

adapter:
	$(CXX) $(CXXFLAGS) src/amhs_adapter_main.cpp -o amhs_adapter

run: dispatcher
	./fabdisp --config config/fab_tools.json --solver cpsat

# --- containerised: the real topology -------------------------------------
infra-up:
	$(COMPOSE) up -d --build
	@echo "UI: http://localhost:8080   (the only published port)"

infra-down:
	$(COMPOSE) down -v

# Assert the network segmentation actually holds.
verify:
	@bash infra/verify-zones.sh

# Prove the boundaries are real by trying to cross them.
reach:
	@bash infra/reachability-test.sh

logs:
	$(COMPOSE) logs -f dispatcher amhs-adapter

# --- viz layer, run locally without docker ---------------------------------
# api/.venv holds flask, confluent-kafka and the anthropic SDK. Fall back to
# python3 so the target still runs where the deps are installed system-wide;
# see api/requirements.txt if the import fails.
API_PY := $(if $(wildcard api/.venv/bin/python3),$(PWD)/api/.venv/bin/python3,python3)

api: scenario
	cd api && SCENARIO_BIN=$(PWD)/fab_scenario TOOL_CONFIG=$(PWD)/config/fab_tools.json \
	  ZONES_FILE=$(PWD)/infra/zones.yaml $(API_PY) main.py

ui:
	cd ui && npm install && npm run dev

clean:
	rm -f fabdisp amhs_adapter fab_scenario hsms_sim fabtest
	rm -rf ui/node_modules ui/dist
