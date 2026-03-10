# Grid Bot CI/CD Pipelines

This directory contains GitHub Actions workflows for continuous integration, testing, and deployment.

## Workflows

### 1. test.yml - Main CI Pipeline
**Triggers:** Push to main/develop, all pull requests

**Jobs:**
- **lint**: Code quality checks (black, flake8, mypy)
- **test**: Unit tests with coverage (Python 3.11 & 3.12)
- **security-scan**: Security vulnerability scanning
- **build-docker**: Docker image build verification

**Coverage Requirement:** 80% minimum

### 2. backtest.yml - Weekly Backtest
**Triggers:** Weekly (Monday 00:00 UTC), manual dispatch

**Purpose:** Runs automated backtests on historical data to validate strategy performance

## Local Testing

### Run Linting
```bash
# Install tools
pip install flake8 black mypy

# Format check
black --check src/ tests/

# Lint
flake8 src/ tests/ --max-line-length=100

# Type check
mypy src/ --ignore-missing-imports
```

### Run Tests
```bash
# Install dependencies
pip install -pip install -pip install -pip install -pi

##########################cp ##########################cp ##########################cp ##########################cp #########term-mi#########

#############################h
# Insta# Insta# Insta# Insta# Insta# Insta# Insta# Insta# Ik dependencie# Insta# Insta# Insta# Insta# Insta# Insta# InstRun ba# Insta# Insta# Insta# Insta# Insta# Insta# Insta#`bash
# # # # # # # # # # # # # # # # # # # # # # # # # un co# # # # # # # # # # # # # # # # # # # # # # # #t

# F# F# F# F# F# F# F# F# F# F# F# F`

## ## ## ## ## ## ## ## ## ## ##these sec## ## ## ## ## ## ## ## ## ## ##these sec## ## ## ## # (Op## ## ## ## ## ## ## ## ## ## ##these sec## ## ## ## ## ## ## ## ## ## ##these sec## #ing
------------KEN` - ---------- For D------Hu-----------g

###########################ur main README.md:

```markdown
[![Grid Bot CI](https:/[![Grid Bot CI](https:/[![Grid Bot CI](flows/Grid%20Bot[![Grid Bot CI](https:/[![Grid b.com/YOU[![Grid Bo/Gri[![Gt/ac[![Grid Bot CI](htthttps://codecov.io/gh/YOUR_USERNAME/Grid_Bot/branch/main/graph/badge.svg)](https://codecov.io/gh/YOUR_USERNAME/Grid_Bot)
```
