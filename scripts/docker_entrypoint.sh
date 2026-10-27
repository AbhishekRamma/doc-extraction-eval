#!/bin/sh
# Runs inside the app container (docker compose up):
#  1. pipeline with the CACHED provider on the synthetic fixtures (no API calls)
#  2. pipeline with the default free rules extractor (or $PROVIDER) on the golden quick split, if present
#  3. load every results file into PostgreSQL
set -e
cd /app
mkdir -p results

python -m src.pipeline --labels tests/fixtures/labels.json --split full \
    --provider cached --cached-dir tests/fixtures/cached_llm --run-id docker_cached_fixtures

if [ -f data/golden/labels.json ]; then
    # No --provider: use the code default (rules-v2) unless PROVIDER is set, e.g. PROVIDER=rules
    python -m src.pipeline --split quick ${PROVIDER:+--provider "$PROVIDER"} --run-id docker_rules_quick
else
    echo "data/golden/labels.json not found: skipping golden run"
fi

python -m src.load_db results/*.json
echo "done: try  docker compose exec db psql -U receipts -d receipts -f /sql/queries.sql"
