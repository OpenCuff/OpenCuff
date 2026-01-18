FILES := $(shell find . -name "*.py" 2>/dev/null || echo "none")

.PHONY: lint test

## Lint Python files
lint:
	echo "Linting $(FILES)"

## Run tests
test:
	echo "Testing"
