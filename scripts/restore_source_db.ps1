# Dump the local Postgres 18 lrmis schema, restore into Docker Postgres 16, rename to irimsv
$env:PGPASSWORD = "admin"
$schema = "lrmis"

# Drop existing source schemas
docker exec schema_mapper_central_db psql -U postgres -d central -c "DROP SCHEMA IF EXISTS irimsv CASCADE; DROP SCHEMA IF EXISTS lrmis CASCADE;"

# Dump from standalone Postgres 18, strip psql 18 meta-commands, restore into Docker Postgres 16
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h localhost -p 5432 -U postgres -d postgres -n $schema --no-owner --no-acl | Where-Object { $_ -notlike '\restrict*' } | docker exec -i schema_mapper_central_db psql -U postgres -d central -q

# Rename schema to irimsv (what the system expects)
docker exec schema_mapper_central_db psql -U postgres -d central -c "ALTER SCHEMA $schema RENAME TO irimsv;"
