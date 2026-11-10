#!/usr/bin/env bash
# Patch GE's Wine exactly as GE-Proton does: run only the "WINE PATCHING" section of GE's own
# patches/protonprep-valve-staging.sh (Valve's Wine + wine-staging + GE's hotfixes and game fixes),
# then snapshot the result as the "ge" commit of $PORT_SRC, a plain git tree the port is applied on.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
prep=$GE_SRC/patches/protonprep-valve-staging.sh
[ -f "$prep" ] || die "run 01-fetch.sh first"

if [ -d "$PORT_SRC/.git" ] && git -C "$PORT_SRC" rev-parse -q --verify refs/tags/ge >/dev/null; then
  echo "READY $PORT_SRC already has the prepped GE tree (tag ge)"; exit 0
fi

stage "Extracting GE's Wine patch steps"
section=$WORK/ge-wine-section.sh
{
  # GE runs this without set -e (patch -N may skip an already-applied patch); so do we
  # GE's helpers (apply_patch, apply_all_in_dir) are defined above the sections
  sed -n '1,/^### (1)/p' "$prep" | sed '$d'
  sed -n '/^### (2) WINE PATCHING ###/,/^### END WINE PATCHING ###/p' "$prep"
} > "$section"
grep -q 'pushd wine' "$section" || die "GE's prep script layout changed; update 02-prep-wine.sh"

stage "Applying GE's Wine patches (this takes a few minutes)"
cd "$GE_SRC"
git -C wine reset -q --hard HEAD
git -C wine clean -qxdf
bash "$section" > "$WORK/ge-prep.log" 2>&1 || { tail -40 "$WORK/ge-prep.log"; die "GE prep failed (log: $WORK/ge-prep.log)"; }
failed=$(grep -c 'FAILED' "$WORK/ge-prep.log" || true)
[ "$failed" = 0 ] || echo "warning: $failed failed hunks in GE's own prep (GE ships the same tree); see $WORK/ge-prep.log"

stage "Snapshotting the prepped tree"
rm -rf "$PORT_SRC"
mkdir -p "$PORT_SRC"
# a copy without Valve's history: the port only needs the prepped files as one commit
tar -C "$GE_SRC/wine" --exclude=.git -cf - . | tar -C "$PORT_SRC" -xf -
cd "$PORT_SRC"
git init -q
git config user.name "proton-uwp"; git config user.email "proton-uwp@localhost"
git config core.autocrlf false
git add -A >/dev/null
git commit -qm "$GE_TAG Wine, prepped by GE's protonprep (wine section)"
git tag ge
echo "READY $PORT_SRC (tag ge)"
