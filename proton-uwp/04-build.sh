#!/usr/bin/env bash
# Build the ported Wine out of tree, configured like Proton's own Wine (new WoW64: x86_64 + i386 PE, one 64-bit
# Unix side). Regenerates what 03-port.py left to generation first. Incremental: rerun after fixing an error.
#
#   ./04-build.sh            regenerate if needed, configure once, make
#   ./04-build.sh --regen    force the regeneration step (after editing a .spec, server/protocol.def, configure.ac)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
[ -d "$PORT_SRC/.git" ] || die "run 03-port.py first"
for tool in autoreconf make gcc flex bison perl x86_64-w64-mingw32-gcc i686-w64-mingw32-gcc; do
  command -v $tool >/dev/null || die "missing build dependency: $tool"
done

cd "$PORT_SRC"
markers=$(git grep -l -e '^<<<<<<< GE-Proton' -e '^>>>>>>> wine-uwp' -- . ':!*.patch' || true)
[ -z "$markers" ] || die "unresolved merge conflicts in: $(echo $markers)"

stamp=$WORK/.regen-$(git rev-parse HEAD)
if [ "${1:-}" = --regen ] || [ ! -f "$stamp" ]; then
  stage "Regenerating server protocol, syscall tables and configure"
  ./tools/make_requests
  # Like Proton's .wine-post-source: generate the Vulkan thunks from the vk.xml/video.xml GE ships in the tree
  # (never the downloading default), then the spec files that depend on them.
  dlls/winevulkan/make_vulkan -x vk.xml -X video.xml
  ./tools/make_specfiles
  # No make_makefiles: GE's own tree does not pass its checks (idl files without makedep pragmas, wdfldr.sys
  # subsystem) and GE's build never runs it. New dlls/headers are listed in configure.ac/include/Makefile.in by hand.
  autoreconf -f
  rm -f "$WORK"/.regen-*; touch "$stamp"
fi

mkdir -p "$BUILD"
cd "$BUILD"
if [ ! -f Makefile ] || [ "$PORT_SRC/configure" -nt config.status ]; then
  stage "Configuring"
  "$PORT_SRC/configure" --enable-archs=x86_64,i386 --disable-tests --without-oss --without-capi \
    > "$WORK/configure.log" 2>&1 || { tail -30 "$WORK/configure.log"; die "configure failed (log: $WORK/configure.log)"; }
  grep -E '^configure: (WARNING|OpenGL|Vulkan)' "$WORK/configure.log" | head -20 || true
fi

stage "Building Wine with $JOBS jobs (log: $WORK/build.log)"
if ! make -j"$JOBS" -k > "$WORK/build.log" 2>&1; then
  echo "--- first errors ---"
  grep -n -E 'error:|Error [0-9]|undefined reference' "$WORK/build.log" | head -40
  die "build failed (full log: $WORK/build.log)"
fi
echo "READY $BUILD/wine"
