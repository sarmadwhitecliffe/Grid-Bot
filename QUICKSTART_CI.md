# CI/CD Quick Start Guide

## Local Testing Commands

### Using Make (Recommended)
```bash
# Show all commands
make help

# Install dependencies
make install

# Run tests with coverage
make test

# Run linting
make lint

# Format code
make format

# Security scan
make security

# Build Docker image
make docker-build

# Start all services
make docker-up

# Stop all services
make docker-down

# Clean cache
make clean
```

### Manual Commands
```bash
# Install dev tools
pip install flake8 black mypy pytest-cov pip-audit safety bandit

# Format code
black src/ tests/

# Lint
flake8 src/ tests/ --max-line-length=100
mypy src/ --ignore-missing-imports

# Test
pytest tests/ -v --cov=src --cov-report=term-missing

# Security
pip-audit
safety check
bandit -r src/ -ll
```

## Docker Quick Start

### Development Stack
```bash
# Start all services (bot, redis, mongodb, grafana, prometheus)
docker-compose up -d

# View logs
docker-compose logs -f grid-bot

# Stop all services
docker-compose down

# Rebuild and restart
docdocdocdocdocdocdocdocdold
`````````````````````````
```bash
# Build
docker build -t grid-bot:latest .

# Run
dododo run -ddododo run -ddododo rv-dododo ru \
                      data \
  -v $(pwd  -v $(pwd  lo  -v
  grid  gridatest

###########er ###########er ot

# Stop
dockerdockerdockerdock& docdockerdockerdock```dockerdockerdockers


ockerdockows
- - - st.yml** - Runs - - - st.yml** t → test → security → docke- - - s)
- **backtes- **ba* - **backtes- **ba*day 00:00 UTC) or manual dispatch

### Trigger Manual Backtest
```bash
# Using GitHub CLI
gh workflow run bagh workflow run bagh workflow run bagh  �gh workflow run bagh workflow runow
gh`

###################################################`:
- **Bot**: Lo- **Bo `do- **Bot**ose - **Bot**: Lo- **
---*Gr---*Gr---*Gr---*Gr--lho---*Gr---*Gr---*Gr--)
---*Grom---*Grom---*Grom---*Grom---*Grom---*Grom---*Grom---*Grom379
- **- **- **- **- **- st:27017

## Pre## Pre## Pre## Pre## re pushing code, run:
```bash
make format  # Format cmake format  # Fo# Chemake format  # Format cmt    make format  # Fosecurity  # Security scan
```

Or all at once:
```bash
make format && make lint && make test && make security
```

## Coverage Reports

After rAfter rAfter rAfter rATerminaAfter rAfter rAge sAfter rAfter rAfter rAfter rAindAf.htmlAfter rAfter rAfter rAfter rATerminaAfter rAfter rAge sAlly
```bash
# Clean cache# Clean cache# Clean cache# Clean cache# Clean cache# Clean cache# Clean cach o# Clean cache# Clean cache# Clean cache# Clean cache# Clean cache# uild # Clean cache# Clean cachees# Clea`
# Clean cache# 
1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1 is up to da1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 
1. Push to GitHub to trigger CI
2. Check [Actions tab](../../actions) for results
3. Fix any failures
4. Add status badges to README
5. Configure GitHub Secrets for deployment
