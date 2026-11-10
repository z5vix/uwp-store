#!/usr/bin/env bash
# Fetch the three sources of the port, pinned: GE-Proton (with its Wine and wine-staging submodules only),
# wine-uwp, and the upstream Wine release wine-uwp is based on. Nothing of Microsoft's is fetched.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
for tool in git curl tar; do command -v $tool >/dev/null || die "missing $tool"; done
mkdir -p "$WORK"

stage "GE-Proton $GE_TAG"
if [ ! -d "$GE_SRC/.git" ]; then
  git clone --depth 1 --branch "$GE_TAG" --no-recurse-submodules "$GE_REPO" "$GE_SRC"
fi
[ "$(git -C "$GE_SRC" describe --tags --exact-match 2>/dev/null)" = "$GE_TAG" ] || die "$GE_SRC is not at $GE_TAG; move it aside"

for sub in wine wine-staging; do
  stage "GE submodule $sub"
  commit=$(git -C "$GE_SRC" ls-tree HEAD "$sub" | awk '{print $3}')
  url=$(git -C "$GE_SRC" config -f .gitmodules "submodule.$sub.url")
  dir=$GE_SRC/$sub
  if [ ! -d "$dir/.git" ] && [ ! -f "$dir/.git" ]; then
    rm -rf "$dir"; git init -q "$dir"; git -C "$dir" remote add origin "$url"
  fi
  if [ "$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null)" != "$commit" ]; then
    git -C "$dir" fetch --depth 1 origin "$commit"
    git -C "$dir" checkout -q --detach FETCH_HEAD
  fi
done

# GE's prep reverts one Valve commit; a shallow clone needs that commit and its parent to do so
revert=$(grep -o 'git revert --no-commit [0-9a-f]\{40\}' "$GE_SRC/patches/protonprep-valve-staging.sh" | awk '{print $4}' || true)
for c in $revert; do
  git -C "$GE_SRC/wine" cat-file -e "$c^{commit}" 2>/dev/null || git -C "$GE_SRC/wine" fetch --depth 2 origin "$c"
done

stage "wine-uwp $WINE_UWP_COMMIT"
if [ ! -d "$UWP_SRC/.git" ]; then
  git init -q "$UWP_SRC"; git -C "$UWP_SRC" remote add origin "$WINE_UWP_REPO"
fi
if [ "$(git -C "$UWP_SRC" rev-parse -q --verify HEAD 2>/dev/null)" != "$WINE_UWP_COMMIT" ]; then
  git -C "$UWP_SRC" fetch --depth 1 origin "$WINE_UWP_COMMIT"
  git -C "$UWP_SRC" checkout -q --detach FETCH_HEAD
fi

stage "Wine $WINE_UWP_BASE_VERSION (merge base)"
if [ ! -d "$BASE_SRC" ]; then
  archive=$WORK/wine-$WINE_UWP_BASE_VERSION.tar.xz
  major=${WINE_UWP_BASE_VERSION%%.*}
  [ -s "$archive" ] || curl -fL --retry 3 "https://dl.winehq.org/wine/source/$major.x/wine-$WINE_UWP_BASE_VERSION.tar.xz" -o "$archive"
  tar -xJf "$archive" -C "$WORK"
fi
echo "READY sources in $WORK"
