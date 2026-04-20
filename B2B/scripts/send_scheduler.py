"""
Send scheduler: respect German business hours (Tue-Thu 10:00-11:30 Europe/Berlin).

Modes:
  --check               print current status only
  --send-if-window      if inside window, send N drafts; else print next window
  --wait-and-send       block until window opens, then send

Usage:
  python send_scheduler.py --check
  python send_scheduler.py --send-if-window --file "..." --count 20
  python send_scheduler.py --wait-and-send --file "..." --count 20

The window logic:
  - Days: Tuesday, Wednesday, Thursday (avoid Monday morning + Friday afternoon)
  - Hours: 10:00 to 11:30 Europe/Berlin local
  - This is 13:30-14:30 or 14:30-15:30 IST depending on DST
"""
import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Berlin')
ALLOWED_WEEKDAYS = {1, 2, 3}  # Mon=0, Tue=1, Wed=2, Thu=3
WINDOW_START_HOUR = 10
WINDOW_END_HOUR = 11
WINDOW_END_MIN = 30  # 11:30


def now_de():
    return dt.datetime.now(TZ)


def is_in_window(t: dt.datetime | None = None) -> bool:
    t = t or now_de()
    if t.weekday() not in ALLOWED_WEEKDAYS:
        return False
    after_start = t.hour > WINDOW_START_HOUR or (t.hour == WINDOW_START_HOUR and t.minute >= 0)
    before_end = t.hour < WINDOW_END_HOUR or (t.hour == WINDOW_END_HOUR and t.minute < WINDOW_END_MIN)
    return after_start and before_end


def next_window_start(t: dt.datetime | None = None) -> dt.datetime:
    t = t or now_de()
    candidate = t.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    if t >= candidate:
        candidate += dt.timedelta(days=1)
    while candidate.weekday() not in ALLOWED_WEEKDAYS:
        candidate += dt.timedelta(days=1)
    return candidate


def fmt_tdelta(td: dt.timedelta) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def run_send(file: str, count: int, no_jitter: bool):
    py = sys.executable
    base = os.path.dirname(os.path.abspath(__file__))
    argv = [py, os.path.join(base, 'send_drafts.py'),
            '--file', file, '--count', str(count)]
    if no_jitter:
        argv.append('--no-jitter')
    print(f"Running: {' '.join(argv)}")
    return subprocess.call(argv)


def run_send_pending(no_jitter: bool):
    py = sys.executable
    base = os.path.dirname(os.path.abspath(__file__))
    argv = [py, os.path.join(base, 'send_pending.py')]
    if no_jitter:
        argv.append('--no-jitter')
    print(f"Running: {' '.join(argv)}")
    return subprocess.call(argv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--send-if-window', action='store_true')
    ap.add_argument('--wait-and-send', action='store_true')
    ap.add_argument('--wait-and-send-pending', action='store_true',
                    help='Wait for window then run send_pending.py (DB-driven)')
    ap.add_argument('--file')
    ap.add_argument('--count', type=int, default=20)
    ap.add_argument('--no-jitter', action='store_true')
    args = ap.parse_args()

    t = now_de()
    in_window = is_in_window(t)
    print(f"Current Europe/Berlin time: {t.strftime('%Y-%m-%d %H:%M:%S %A')}")
    print(f"In send window: {in_window}")
    if not in_window:
        nxt = next_window_start(t)
        delta = nxt - t
        print(f"Next window opens: {nxt.strftime('%Y-%m-%d %H:%M %A')} ({fmt_tdelta(delta)} from now)")
    else:
        end = t.replace(hour=WINDOW_END_HOUR, minute=WINDOW_END_MIN, second=0, microsecond=0)
        print(f"Window closes: {end.strftime('%H:%M')} ({fmt_tdelta(end - t)} left)")

    if args.check:
        return 0

    if not args.file:
        print("--file required for send actions", file=sys.stderr)
        return 2

    if args.send_if_window:
        if in_window:
            return run_send(args.file, args.count, args.no_jitter)
        print("Outside window, not sending. Use --wait-and-send to block, or retry later.")
        return 0

    if args.wait_and_send:
        if not in_window:
            nxt = next_window_start(t)
            wait_s = (nxt - t).total_seconds()
            print(f"Sleeping {fmt_tdelta(nxt - t)}...")
            time.sleep(wait_s + 5)
        return run_send(args.file, args.count, args.no_jitter)

    if args.wait_and_send_pending:
        if not in_window:
            nxt = next_window_start(t)
            wait_s = (nxt - t).total_seconds()
            print(f"Sleeping {fmt_tdelta(nxt - t)}...")
            time.sleep(wait_s + 5)
        return run_send_pending(args.no_jitter)

    print("Nothing to do. Use --check, --send-if-window, --wait-and-send, "
          "or --wait-and-send-pending.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
