.PHONY: install dev test lint format check clean run run-dev help

## Install dependencies
install:
	uv sync

## Set up development environment (install + pre-commit hooks)
dev: install
	uv run pre-commit install

## Run all tests
test:
	uv run pytest

## Run tests with verbose output
test-verbose:
	uv run pytest -v

## Run tests with coverage
test-coverage:
	uv run pytest --cov=opencuff --cov-report=term-missing

## Run linter
lint:
	uv run ruff check .

## Run linter and fix auto-fixable issues
lint-fix:
	uv run ruff check . --fix

## Run formatter
format:
	uv run ruff format .

## Check formatting without making changes
format-check:
	uv run ruff format --check .

## Run all checks (lint + format check + tests)
check: lint format-check test

## Run the MCP server with inspector (development mode)
run-dev:
	uv run fastmcp dev src/opencuff/server.py:mcp

## Run the MCP server (production mode)
run:
	uv run fastmcp run src/opencuff/server.py:mcp

## Clean build artifacts and caches
clean:
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf __pycache__
	rm -rf src/opencuff/__pycache__
	rm -rf src/opencuff/plugins/__pycache__
	rm -rf src/opencuff/plugins/builtin/__pycache__
	rm -rf src/opencuff/plugins/adapters/__pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

## Show help
help:
	@echo "OpenCuff Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install      - Install dependencies"
	@echo "  make dev          - Set up development environment"
	@echo ""
	@echo "Testing:"
	@echo "  make test         - Run all tests"
	@echo "  make test-verbose - Run tests with verbose output"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint         - Run linter"
	@echo "  make lint-fix     - Run linter and fix issues"
	@echo "  make format       - Format code"
	@echo "  make format-check - Check formatting"
	@echo "  make check        - Run all checks (lint + format + test)"
	@echo ""
	@echo "Running:"
	@echo "  make run-dev      - Run MCP server with inspector"
	@echo "  make run          - Run MCP server"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean        - Clean build artifacts"
