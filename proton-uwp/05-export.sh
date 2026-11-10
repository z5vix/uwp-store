#!/usr/bin/env bash
# Save hand-made fixes in $PORT_SRC (commits after tag port-auto) as resolutions/*.patch in this repository,
# so 03-port.py reproduces the finished port from clean sources. Commit your fixes in $PORT_SRC first.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
[ -z "$(git -C "$PORT_SRC" status --porcelain)" ] || die "$PORT_SRC has uncommitted changes; commit them first"
rm -f "$here"/resolutions/*.patch
mkdir -p "$here/resolutions"
git -C "$PORT_SRC" format-patch -q --no-signature --zero-commit -o "$here/resolutions" port-auto..HEAD
ls "$here/resolutions"
