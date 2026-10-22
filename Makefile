.PHONY: install test lint policy-test tf-validate check immudb-up scalability drift analyze

install:
	pip install -e ".[immudb,dev]"

test:
	pytest -q

lint:
	ruff check tdgov experiments tests

policy-test:
	opa test policies -v

tf-validate:
	cd blueprint && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate

check: lint test policy-test tf-validate

immudb-up:
	docker compose up -d immudb

scalability:
	python -m experiments.scalability.run_scalability --sizes 10 50 100 200 400 --reps 3 --out results/scalability.csv
	python -m experiments.analyze --scalability results/scalability.csv

drift:
	python -m experiments.drift_injection.run_experiment --config tdgov.yaml --node $(NODE) --reps 5 --interval 120 --out results/drift_injection.csv

analyze:
	python -m experiments.analyze results/drift_injection.csv
