.PHONY: up down logs insert worker worker-loop schema-monitor onboard onboard-auto onboard-multi refresh refresh-multi status test psql-central mysql-staging

up:
	docker compose up -d
	@echo "Waiting for PostgreSQL and MySQL to be ready..."

down:
	docker compose down

logs:
	docker compose logs -f

insert:
	python scripts/insert_sample_row.py

worker:
	python -m src.worker

worker-loop:
	python -m src.worker --loop

schema-monitor:
	python scripts/schema_monitor.py

onboard:
	python -m src.pipeline onboard --source-schema $(SCHEMA) --source-table $(TABLE) --target-system $(TARGET)

onboard-auto:
	python -m src.pipeline onboard --source-schema $(SCHEMA) --source-table $(TABLE) --target-system $(TARGET) --auto

onboard-multi:
	python -m src.pipeline onboard --source-schema $(SCHEMA) --source-table $(TABLES) --target-system $(TARGET)

refresh:
	python -m src.pipeline refresh --source-schema $(SCHEMA) --source-table $(TABLE) --target-system $(TARGET)

refresh-multi:
	python -m src.pipeline refresh --source-schema $(SCHEMA) --source-table $(TABLES) --target-system $(TARGET)

status:
	python -m src.pipeline status

test:
	pytest -q

psql-central:
	docker exec -it schema_mapper_central_db psql -U postgres -d central

mysql-staging:
	docker exec -it schema_mapper_lrmis_staging mysql -u irimsv_writer -pchange-me lrmis_staging
