install:
	pip install -r requirements.txt

test:
	GOOGLE_API_KEY=dummy pytest tests/

docker-build:
	docker build -t continuity-app -f Dockerfile .

deploy:
	docker compose up -d --build

POSTGRES_CONTAINER ?= continuity-postgres
POSTGRES_DB ?= continuity
REDIS_CONTAINER ?= $$(docker compose ps -q redis)
BACKUP_DIR ?= ./backups
TIMESTAMP ?= $$(date +%Y%m%d-%H%M%S)

update:
	git pull origin main
	pip install -r requirements.txt
	docker restart continuity-server continuity-worker

backup:
	mkdir -p $(BACKUP_DIR)
	docker exec -T $(POSTGRES_CONTAINER) pg_dump -Fc $(POSTGRES_DB) > $(BACKUP_DIR)/postgres-$(TIMESTAMP).dump
	docker cp $(REDIS_CONTAINER):/data/appendonly.aof $(BACKUP_DIR)/redis-$(TIMESTAMP).aof

prune:
	docker image prune -f
	find ./outputs -type f \( -name '*.tmp' -o -name '*.partial' -o -name '*.crash' \) -print -delete

stop:
	docker compose down

logs:
	docker compose logs -f

logs-worker:
	docker logs -f continuity-worker
