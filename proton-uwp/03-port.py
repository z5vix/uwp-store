#!/usr/bin/env python3
"""Port wine-uwp onto GE-Proton's Wine with a three-way merge per file.

wine-uwp vendors whole files that it changed on top of upstream Wine 11.20. For each of them:

    base   = upstream 11.20's file            (what wine-uwp started from)
    theirs = wine-uwp's file                  (base + wine-uwp's change)
    ours   = GE's prepped file                (Valve 11.0 + staging + GE)

git merge-file replays base->theirs onto ours. Regions wine-uwp changed that upstream also moved between
11.0 and 11.20 come out as conflicts, which are listed in PORT-STATUS.md. Generated files (syscall tables,
the server protocol, configure) are not merged: they are regenerated from the merged sources afterwards.

Hand resolutions live in resolutions/*.patch next to this script (exported by 05-export.sh) and are applied
on top of the automatic merge, so the port is reproducible from a clean checkout.

Result in $PORT_SRC: tag "ge" (GE's tree), tag "port-auto" (automatic merge, conflict markers included),
then one commit per resolution patch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def env() -> dict[str, str]:
    out = subprocess.run(["bash", "-c", f"set -a; . '{HERE}/env.sh'; env -0"], capture_output=True, check=True)
    pairs = [p.split("=", 1) for p in out.stdout.decode().split("\0") if "=" in p]
    return dict(pairs)


E = env()
WORK, UWP, BASE, PORT = (Path(E[k]) for k in ("WORK", "UWP_SRC", "BASE_SRC", "PORT_SRC"))

# Produced by Wine's tools/make_requests, make_specfiles, make_makefiles and autoreconf. Taken from GE and
# regenerated after the merge, never merged by hand.
GENERATED = {
    "configure",
    "include/wine/server_protocol.h",
    "server/request_handlers.h",
    "server/request_trace.h",
    "dlls/ntdll/ntsyscalls.h",
    "dlls/win32u/win32syscalls.h",
    "include/config.h.in",
}


def git(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(PORT), *args], check=check, text=True, capture_output=True, **kw)


def files() -> list[str]:
    out = []
    for line in (UWP / "wine" / "FILES").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def merge(ours: Path, base: Path | None, theirs: Path) -> tuple[bytes, int]:
    base_path = str(base) if base else os.devnull
    result = subprocess.run(
        ["git", "merge-file", "-p", "--diff3", "--diff-algorithm=histogram",
         "-L", "GE-Proton", "-L", "upstream-11.20", "-L", "wine-uwp", str(ours), base_path, str(theirs)],
        capture_output=True)
    if result.returncode < 0:
        raise SystemExit(f"git merge-file failed on {ours}: {result.stderr.decode()}")
    return result.stdout, result.returncode


def main() -> int:
    if not git("rev-parse", "-q", "--verify", "refs/tags/ge", check=False).stdout:
        print("run 02-prep-wine.sh first", file=sys.stderr)
        return 1
    git("checkout", "-q", "-f", "ge")
    git("clean", "-qfdx")
    git("tag", "-d", "port-auto", check=False)
    git("branch", "-D", "port", check=False)
    git("checkout", "-q", "-b", "port")

    report = {"clean": [], "conflict": {}, "new": [], "new_vs_ge": [], "added_upstream": [], "generated": []}
    for rel in files():
        theirs = UWP / "wine" / rel
        base = BASE / rel
        ours = PORT / rel
        if not theirs.exists():
            continue
        if rel in GENERATED:
            report["generated"].append(rel)
            continue
        if not base.exists():
            if not ours.exists():
                ours.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(theirs, ours)
                report["new"].append(rel)
                continue
            # wine-uwp and GE both added this file: merge against an empty base
            data, conflicts = merge(ours, None, theirs)
            ours.write_bytes(data)
            (report["conflict"].__setitem__(rel, conflicts) if conflicts else report["new_vs_ge"].append(rel))
            continue
        if not ours.exists():
            # added upstream after 11.0: wine-uwp's whole file, which may lean on other post-11.0 changes
            ours.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(theirs, ours)
            report["added_upstream"].append(rel)
            continue
        data, conflicts = merge(ours, base, theirs)
        ours.write_bytes(data)
        if conflicts:
            report["conflict"][rel] = conflicts
        else:
            report["clean"].append(rel)

    git("add", "-A")
    git("commit", "-qm", "port: automatic three-way merge of wine-uwp onto " + E["GE_TAG"] + " (conflicts marked)")
    git("tag", "port-auto")

    applied = []
    for patch in sorted((HERE / "resolutions").glob("*.patch")):
        result = subprocess.run(["git", "-C", str(PORT), "am", "-q", "--3way", str(patch)], capture_output=True, text=True)
        if result.returncode != 0:
            subprocess.run(["git", "-C", str(PORT), "am", "--abort"], capture_output=True)
            print(f"resolution {patch.name} no longer applies:\n{result.stderr}", file=sys.stderr)
            return 1
        applied.append(patch.name)

    remaining = {}
    for rel in report["conflict"]:
        path = PORT / rel
        if path.exists():
            count = sum(1 for line in path.read_bytes().splitlines() if line.startswith(b"<<<<<<< "))
            if count:
                remaining[rel] = count
    write_status(report, applied, remaining)
    total = sum(remaining.values())
    print(f"clean {len(report['clean'])}, new {len(report['new'])}, added-upstream {len(report['added_upstream'])}, "
          f"generated {len(report['generated'])}, conflicted files {len(report['conflict'])} "
          f"(after resolutions: {len(remaining)} files, {total} hunks)")
    return 0 if not remaining else 3


def write_status(report: dict, applied: list[str], remaining: dict[str, int]) -> None:
    (WORK / "port-report.json").write_text(json.dumps({**report, "remaining": remaining, "applied": applied}, indent=1))
    lines = [
        "# Port status", "",
        f"wine-uwp `{E['WINE_UWP_COMMIT'][:12]}` (Wine {E['WINE_UWP_BASE_VERSION']}) onto `{E['GE_TAG']}`.", "",
        "| | files |", "| --- | --- |",
        f"| merged cleanly | {len(report['clean'])} |",
        f"| new in wine-uwp, copied | {len(report['new'])} |",
        f"| added upstream after 11.0, copied whole | {len(report['added_upstream'])} |",
        f"| generated, regenerated after merge | {len(report['generated'])} |",
        f"| conflicted in the automatic merge | {len(report['conflict'])} |",
        f"| still conflicted after resolutions/ | {len(remaining)} ({sum(remaining.values())} hunks) |", "",
        f"Resolution patches applied: {', '.join(applied) or 'none'}", "",
    ]
    if remaining:
        lines += ["## Still to resolve", "", "| file | hunks |", "| --- | --- |"]
        lines += [f"| `{f}` | {n} |" for f, n in sorted(remaining.items(), key=lambda kv: -kv[1])]
        lines.append("")
    if report["added_upstream"]:
        lines += ["## Files added upstream after 11.0", "",
                  "Copied whole from wine-uwp; check they do not need other post-11.0 changes.", ""]
        lines += [f"- `{f}`" for f in report["added_upstream"]]
        lines.append("")
    (HERE / "PORT-STATUS.md").write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
