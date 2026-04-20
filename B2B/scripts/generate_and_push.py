"""
Convenience: generate drafts then immediately push to Outlook.

Runs generate_drafts.py then write_to_outlook.py on the same file.
Accepts the same --file and --limit as generate_drafts.

Usage:
    python generate_and_push.py --file "<batch.xlsx>"
    python generate_and_push.py --file "<batch.xlsx>" --limit 5
"""
import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def run(argv):
    print(f"\n>>> {' '.join(argv)}")
    return subprocess.call(argv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()

    py = sys.executable

    gen_argv = [py, os.path.join(BASE, 'generate_drafts.py'), '--file', args.file]
    if args.limit:
        gen_argv += ['--limit', str(args.limit)]
    rc = run(gen_argv)
    if rc != 0:
        print(f"generate_drafts exited {rc}; aborting push.")
        return rc

    push_argv = [py, os.path.join(BASE, 'write_to_outlook.py'), '--file', args.file]
    rc = run(push_argv)
    return rc


if __name__ == '__main__':
    sys.exit(main())
