"""Command line: python3 -m contacts.cli [--file PATH] add NAME [--phone P] [--email E] | report"""
import argparse
import sys

from . import report, store

DEFAULT_FILE = "data/contacts.json"


def build_parser():
    parser = argparse.ArgumentParser(prog="contacts")
    parser.add_argument("--file", default=DEFAULT_FILE)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--phone")
    add.add_argument("--email")
    sub.add_parser("report")
    return parser


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if args.command == "add":
        store.add(args.file, args.name, args.phone, args.email)
        print("added", file=out)
    else:
        print(report.render(args.file), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
