.PHONY: build all

%.o: %.c
	gcc -c $< -o $@

## Build the project
build:
	echo "build"

all: build
	echo "all"
