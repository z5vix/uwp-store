# Shared settings for the GE-Proton UWP pipeline. Sourced by every step; override any of these in the environment.
GE_TAG=${GE_TAG:-GE-Proton11-9}
GE_REPO=${GE_REPO:-https://github.com/GloriousEggroll/proton-ge-custom.git}
WINE_UWP_REPO=${WINE_UWP_REPO:-https://github.com/designgears/wine-uwp.git}
WINE_UWP_COMMIT=${WINE_UWP_COMMIT:-ac56cede08bbb99c5b88dd7b54e752f709cd6b47}
# wine-uwp vendors whole files from this upstream release; it is the merge base of the port
WINE_UWP_BASE_VERSION=${WINE_UWP_BASE_VERSION:-11.20}

WORK=${PROTON_UWP_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/uwp-store/proton-uwp}
GE_SRC=$WORK/proton-ge-custom          # GE's tree; wine/ inside it is prepped (patched) by prep-wine.sh
UWP_SRC=$WORK/wine-uwp                 # wine-uwp checkout
BASE_SRC=$WORK/wine-$WINE_UWP_BASE_VERSION
PORT_SRC=$WORK/wine-port               # git tree: GE's prepped Wine, then the port commit on top
BUILD=$WORK/build                      # out-of-tree Wine build
DIST=$WORK/dist                        # wine-uwp distribution built from the port
TOOL_NAME=${TOOL_NAME:-$GE_TAG-UWP}
DATA_HOME=${UWP_STORE_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/uwp-store}
RUNTIME_OUT=$DATA_HOME/runtimes/$TOOL_NAME
JOBS=${JOBS:-$(( $(nproc) > 2 ? $(nproc) - 2 : 1 ))}

stage() { echo "STAGE $*"; }
die() { echo "error: $*" >&2; exit 1; }
