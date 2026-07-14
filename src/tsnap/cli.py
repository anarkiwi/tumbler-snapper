"""tsnap console entry: dispatch to the recover / tracker subcommands."""

from __future__ import annotations
import argparse
import sys
from tsnap import curate, recover, tracker


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(prog="tsnap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("recover", help="recover per-frame register generators", add_help=False)
    sub.add_parser("tracker", help="build the tracker IR", add_help=False)
    sub.add_parser("curate", help="build the HVSC fixture manifest", add_help=False)
    dispatch = {"recover": recover.main, "tracker": tracker.main, "curate": curate.main}
    if argv and argv[0] in dispatch:
        dispatch[argv[0]](argv[1:])
        return None
    parser.parse_args(argv)
    return None


if __name__ == "__main__":
    main()
