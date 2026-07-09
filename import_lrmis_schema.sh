#!/bin/bash
# Import LRMIS target schema into Docker MySQL
# Usage: import_lrmis_schema.sh /path/to/lrmis.sql
#
# The LRMIS dump is not included in the repository. Obtain it from the LRMIS
# team and pass its path as the first argument.

DUMP_FILE="${1:-}"

if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
  echo "ERROR: LRMIS dump file not found."
  echo ""
  echo "Usage: import_lrmis_schema.sh /path/to/lrmis.sql"
  echo ""
  echo "Obtain the LRMIS schema dump and pass its path as an argument."
  exit 1
fi

echo "=== Importing LRMIS Schema into Docker MySQL ==="

echo "[1/2] Copying LRMIS schema to container..."
docker cp "$DUMP_FILE" schema_mapper_lrmis_staging:/tmp/lrmis_full.sql

echo "[2/2] Importing schema into MySQL..."
docker exec -i schema_mapper_lrmis_staging mysql -u root -proot lrmis_staging < /tmp/lrmis_full.sql

echo "=== LRMIS Schema Import Complete ==="
echo ""
echo "Tables imported. You can now run:"
echo "  python -m src.pipeline discover --source-schema irimsv"
