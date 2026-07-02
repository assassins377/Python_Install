.PHONY: install test lint format typecheck security clean build run

install:
	pip install -r requirements.txt
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v --tb=short

test-cov:
	python -m pytest tests/ -v --tb=short --cov --cov-report=term-missing

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy --ignore-missing-imports .

security:
	bandit -r . -x ./tests,./tools,./.venv,./build,./dist -ll

check: lint typecheck security test

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ htmlcov/ .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

build:
	pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x86 --icon=icons/system.png main.py

run:
	python main.py

run-cli:
	python main.py --list
