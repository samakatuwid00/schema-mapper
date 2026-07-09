#!/bin/bash
# Import IRIMSV source data into Docker PostgreSQL
# Usage: import_irimsv_data.sh /path/to/irimsv.sql
#
# The IRIMSV dump file is not included in the repository.
# Obtain the dump from your DBA or export it from your IRIMSV instance,
# then pass the path as the first argument.

DUMP_FILE="${1:-sql/irimsv.sql}"

if [ ! -f "$DUMP_FILE" ]; then
  echo "ERROR: IRIMSV dump file not found at '$DUMP_FILE'"
  echo ""
  echo "Please obtain the dump from your IRIMSV database and either:"
  echo "  1. Place it at sql/irimsv.sql, or"
  echo "  2. Pass the path as an argument: import_irimsv_data.sh /path/to/irimsv.sql"
  exit 1
fi

echo "=== Importing IRIMSV Source Data into Docker PostgreSQL ==="

echo "[1/3] Copying IRIMSV dump to container..."
docker cp "$DUMP_FILE" schema_mapper_central_db:/tmp/irimsv.dump

echo "[2/3] Cleaning up existing schema..."
docker exec schema_mapper_central_db psql -U postgres -d central -c "DROP SCHEMA IF EXISTS irimsv CASCADE;"

echo "[3/3] Restoring IRIMSV data..."
docker exec -i schema_mapper_central_db pg_restore -U postgres -d central --no-owner --no-privileges /tmp/irimsv.dump 2>&1 || true

echo ""
echo "=== IRIMSV Data Import Complete ==="
echo ""
echo "Verify tables:"
echo "  docker exec schema_mapper_central_db psql -U postgres -d central -c '\dt irimsv.*'"
echo ""
echo "Next steps:"
echo "  python -m src.pipeline discover --source-schema irimsv"
