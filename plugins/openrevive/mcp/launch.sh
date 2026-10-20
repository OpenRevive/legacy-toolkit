#!/bin/sh
# The host resolves cwd relative to the plugin root. Resolve again here so
# startup never relies on the user's checkout or the invoking shell's cwd.
set -eu
MCP_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec node "$MCP_DIRECTORY/server.mjs" "$@"
