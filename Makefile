.PHONY: help install test lint format security docker-build docker-up docker-down clean

help:
	@echo "Grid Bot - Available Commands"
	@echo "=============================="
	@echo "install       - Install dependencies"
	@echo "test          - Run tests with coverage"
	@echo "lint          - Run code quality checks"
	@echo "format        - Format code with black"
	@echo "security      - Run security scans"
	@echo "docker-build  - Build Docker image"
	@echo "docker-up     - Start Docker services"
	@echo "docker-down   - Stop Docker services"
	@echo "clean         - Clean cache and temp files"

install:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install flake8 black mypy pytest-cov pip-audit safety bandit

test:
	pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-report=html

lint:
	@echo "Running Black check..."
	black --check src/ tests/
	@echo "Running Flake8..."
	flake8 src/ tests/ --max-line-length=100 --extend-ignore=E203,W503
	@echo "Running mypy..."
	mypy src/ --ignore-missing-imports

format:
	black src/ tests/
	@echo "Code formatted successfully!"

security:
	@echo "Checking for hardcoded secrets..."
	@! grep -r "sk-" src/ tests/ || (echo "Found potential secrets!" && exit 1)
	@echo "Running pip-audit..."
	pip-audit || true
	@echo "Running safety..."
	safety check || true
	@echo "Running bandit..."
	bandit -r src/ -ll

docker-build:
	docker build -t grid-bot:latest .

docker-up:
	docker-compose up -d
	@echo "Services started. Check logs with: docker-compose logs -f"

docker-down:
	docker-compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/
	rm -f .coverage
	@echo "Cleaned cache and temporary files!"
