"""Command line: python3 -m todo.cli [--file PATH] add TITLE [--due YYYY-MM-DD] [--priority normal|high] | done ID | list

Exit codes: 0 fine, 1 for an unknown task id, 2 for a bad argument (message `error: ...` on stderr).
"""
import argparse
import sys

from . import render, store

DEFAULT_FILE = "tasks.json"


def build_parser():
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--file", default=DEFAULT_FILE)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("--due")
    add.add_argument("--priority", default="normal")
    done = sub.add_parser("done")
    done.add_argument("id", type=int)
    sub.add_parser("list")
    return parser


def main(argv=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        if args.command == "add":
            task = store.add_task(args.file, args.title, args.due, args.priority)
            print(f"added {task.id}", file=out)
        elif args.command == "done":
            store.mark_done(args.file, args.id)
            print(f"done {args.id}", file=out)
        else:
            print(render.render_list(store.load(args.file)), file=out)
    except ValueError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except KeyError:
        print(f"error: no task {args.id}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
