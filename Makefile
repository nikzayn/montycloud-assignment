.PHONY: install test up deploy down logs

install:            ## Install dev dependencies
	pip install -r requirements-dev.txt

test:               ## Run unit tests with coverage (no Docker needed)
	python -m pytest

up:                 ## Start LocalStack
	docker compose up -d

deploy:             ## Create table, bucket, Lambdas and API in LocalStack
	python scripts/deploy_local.py

down:               ## Stop LocalStack (all local data is discarded)
	docker compose down

logs:               ## Follow LocalStack logs (Lambda output shows up here)
	docker compose logs -f localstack