#!/bin/bash
# Copy the whole remote/ directory to the old Mac, then run this file.
set -euo pipefail
exec /usr/bin/python "$(dirname "$0")/bootstrap.py" "$@"
