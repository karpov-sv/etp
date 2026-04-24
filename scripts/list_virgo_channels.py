"""List channels available in a virgotools FrameFile source.

Uses the undocumented-in-Python but stable C-level helpers exposed via PyFd
(FrFileIGetAdcNames / ...Proc / ...Ser / ...Sim) to retrieve the channel names
without having to read any frame payload.

Requires the igwn conda environment (virgotools, PyFd). Run with:

    conda activate igwn
    python scripts/list_virgo_channels.py --source trend --contains DAQ

Kinds:
    adc   raw ADC channels (default; this is what 'trend' contains)
    proc  processed channels
    sms   slow-monitoring / serial channels
    sim   simulation channels
    all   try all four

Filters are ANDed together when more than one is given.
"""

from __future__ import annotations

import argparse
import ctypes
import re
import sys


def _load_virgotools():
    try:
        import PyFd  # noqa: F401
        from virgotools import FrameFile
    except ImportError as exc:
        sys.exit(
            f"error: {exc}\n"
            "This script needs the igwn conda env. Run:\n"
            "  source /cvmfs/software.igwn.org/conda/etc/profile.d/conda.sh\n"
            "  conda activate igwn"
        )
    return PyFd, FrameFile


def _names_from_vect(vec_ptr):
    if not vec_ptr:
        return []
    v = vec_ptr.contents
    n = int(v.nData)
    out = []
    for i in range(n):
        s = v.dataQ[i]
        if s:
            out.append(ctypes.string_at(s).decode("utf-8", errors="replace"))
    return out


def _fetch_names(fn, fr_file, retries=2, delay_s=0.5):
    import time
    for _ in range(retries + 1):
        p = fn(fr_file)
        if p:
            return p
        time.sleep(delay_s)
    return None


def list_channels(ff, kinds, PyFd):
    fns = {
        "adc": PyFd.FrFileIGetAdcNames,
        "proc": PyFd.FrFileIGetProcNames,
        "sms": PyFd.FrFileIGetSerNames,
        "sim": PyFd.FrFileIGetSimNames,
    }
    result = {}
    for kind in kinds:
        p = _fetch_names(fns[kind], ff._FrFile)
        try:
            result[kind] = _names_from_vect(p)
        finally:
            if p:
                PyFd.FrVectFree(p)
    return result


def _matcher(prefix, contains, regex):
    tests = []
    if prefix:
        tests.append(lambda n, p=prefix: n.startswith(p))
    if contains:
        tests.append(lambda n, s=contains: s in n)
    if regex:
        pat = re.compile(regex)
        tests.append(lambda n, r=pat: r.search(n) is not None)
    if not tests:
        return lambda _n: True
    return lambda n: all(t(n) for t in tests)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", default="trend",
        help="virgotools FrameFile source (e.g. trend, raw; default: trend)",
    )
    parser.add_argument(
        "--kind", default="adc", choices=("adc", "proc", "sms", "sim", "all"),
        help="channel kind to list (default: adc)",
    )
    parser.add_argument("--prefix", help="keep only channels starting with this prefix")
    parser.add_argument("--contains", help="keep only channels containing this substring")
    parser.add_argument("--regex", help="keep only channels matching this regex (re.search)")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="print at most N channels (0 = no limit)",
    )
    parser.add_argument(
        "--count-only", action="store_true",
        help="print only the number of matching channels per kind",
    )
    parser.add_argument(
        "--show-kind", action="store_true",
        help="prefix each line with the kind (adc/proc/sms/sim)",
    )
    args = parser.parse_args(argv)

    PyFd, FrameFile = _load_virgotools()

    kinds = ("adc", "proc", "sms", "sim") if args.kind == "all" else (args.kind,)
    match = _matcher(args.prefix, args.contains, args.regex)

    print(f"# opening FrameFile({args.source!r}) ...", file=sys.stderr)
    with FrameFile(args.source) as ff:
        print(
            f"# gps_start={ff.gps_start} gps_end={ff.gps_end}",
            file=sys.stderr,
        )
        found = list_channels(ff, kinds, PyFd)

    total_matched = 0
    for kind in kinds:
        names = [n for n in found[kind] if match(n)]
        total_matched += len(names)
        if args.count_only:
            print(f"{kind}: matched={len(names)} of total={len(found[kind])}")
            continue
        if args.kind == "all":
            print(f"# --- {kind}: {len(names)} matched / {len(found[kind])} total ---",
                  file=sys.stderr)
        out = names if args.limit <= 0 else names[: args.limit]
        for n in out:
            if args.show_kind:
                print(f"{kind}\t{n}")
            else:
                print(n)
        if args.limit and len(names) > args.limit:
            print(
                f"# ... {len(names) - args.limit} more ({kind}) truncated by --limit",
                file=sys.stderr,
            )

    if not args.count_only:
        print(f"# total matched: {total_matched}", file=sys.stderr)


if __name__ == "__main__":
    main()
