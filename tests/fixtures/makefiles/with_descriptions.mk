.PHONY: build deploy test lint format

## Format 1: Comment above target
build:
	echo "build"

deploy: ## Format 2: Inline comment
	echo "deploy"

# Regular comment (not a description)
test:
	echo "test"

lint: deps ## Format 2 with prerequisites: Run linting
	echo "lint"

## Multi-word description for format target
format:
	echo "format"
