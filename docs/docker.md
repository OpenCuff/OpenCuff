# OpenCuff Docker Container

This container runs the OpenCuff MCP server (FastMCP) and loads your
configuration from `settings.yml`. The server runs over stdio, so it is intended
to be started by an MCP client (Claude Code, etc.) or manually with `docker run`.

## Build the image

```bash
docker build -t opencuff:local -f docker/Dockerfile .
```

## Run manually

Mount your project and settings file so OpenCuff can read them:

```bash
docker run --rm -i \
  -v /path/to/your/project:/workspace \
  -v /path/to/settings.yml:/settings.yml \
  -e OPENCUFF_SETTINGS=/settings.yml \
  opencuff:local
```

Notes:

- The container runs the MCP server via `fastmcp run /app/src/opencuff/server.py:mcp`.
- `OPENCUFF_SETTINGS` is the preferred way to point at the settings file.
- The working directory is `/workspace`, so relative paths in `settings.yml`
  resolve against the mounted project directory.

## Use from an MCP client (example)

For a client that accepts a command + args (e.g., Claude Code), configure it to
start the container:

```json
{
  "mcpServers": {
    "opencuff": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/path/to/your/project:/workspace",
        "-v", "/path/to/settings.yml:/settings.yml",
        "-e", "OPENCUFF_SETTINGS=/settings.yml",
        "opencuff:local"
      ]
    }
  }
}
```

## How commands affect the host / devcontainer

OpenCuff executes plugin commands *inside the container*. For those commands to
affect your project or environment:

- **Host filesystem changes**: bind-mount the project into `/workspace`.
  Commands like `make` or `npm` will modify files inside that mount, so changes
  are reflected on the host.
- **Host tooling**: the container only has Python and OpenCuff. If your plugins
  call tools such as `make`, `node`, or `pnpm`, build a derived image that
  installs those tools, or run OpenCuff inside a devcontainer that already has
  them.
- **Devcontainer parity**: when OpenCuff runs in the same devcontainer as your
  editor, commands execute in that shared environment, so they are effective
  without extra mounts or tool installation on the host.

If you need different tooling or paths, copy `docker/Dockerfile` and extend it
to install your required dependencies.
