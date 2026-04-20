"""
One-click orchestrator: pick -> generate -> push to Outlook -> (send|schedule|skip).

Prints progress markers to stdout so the backend job log shows stage transitions:
    [STAGE] pick
    [STAGE] generate
    [STAGE] outlook
    [STAGE] send  (or [STAGE] schedule, or [STAGE] skip)
    [DONE] pipeline complete

Usage:
    python run_pipeline.py --industry "Commerce" --count 20 --send-mode schedule
    python run_pipeline.py --industry "Health Care" --count 10 --send-mode draft   (no send)
    python run_pipeline.py --industry "Finance" --count 20 --send-mode now
"""
import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
BATCHES_DIR = Path(r'H:/Lead Generator/B2B/Database/Marcel Data/01_Daily_Batches')
PY = sys.executable


def stage(name):
    print(f"\n[STAGE] {name}", flush=True)


def run(argv, label):
    print(f">>> {label}", flush=True)
    rc = subprocess.call(argv)
    if rc != 0:
        print(f"[ERROR] {label} exited {rc}", flush=True)
        sys.exit(rc)


def newest_batch_for_industry(industry: str) -> Path | None:
    today = dt.date.today().isoformat()
    safe = industry.replace('/', '_').replace(' ', '_')
    candidate = BATCHES_DIR / f'{today}_{safe}.xlsx'
    if candidate.exists():
        return candidate
    # Fallback — newest file matching today+industry-ish
    matches = list(BATCHES_DIR.glob(f'{today}_{safe}*.xlsx'))
    if matches:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matches[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--industry', required=True)
    ap.add_argument('--count', type=int, required=True)
    ap.add_argument('--tier', type=int, choices=[1, 2], default=None)
    ap.add_argument('--send-mode', choices=['now', 'schedule', 'draft'],
                    default='schedule',
                    help='now=send immediately, schedule=wait for Germany window, draft=stop after Outlook')
    ap.add_argument('--no-jitter', action='store_true')
    args = ap.parse_args()

    # --- Stage 1: Pick ---
    stage('pick')
    pick_argv = [PY, str(BASE / 'pick_batch.py'),
                 '--industry', args.industry, '--count', str(args.count)]
    if args.tier:
        pick_argv += ['--tier', str(args.tier)]
    run(pick_argv, f"pick_batch: {args.industry} x {args.count}")

    batch = newest_batch_for_industry(args.industry)
    if not batch:
        print(f"[ERROR] batch file not created for {args.industry}", flush=True)
        sys.exit(2)
    print(f"[BATCH] {batch.name}", flush=True)

    # --- Stage 2: Generate drafts ---
    stage('generate')
    run([PY, str(BASE / 'generate_drafts.py'), '--file', str(batch)],
        'generate_drafts')

    # --- Stage 3: Push to Outlook ---
    stage('outlook')
    run([PY, str(BASE / 'write_to_outlook.py'), '--file', str(batch)],
        'write_to_outlook')

    # --- Stage 4: Send (or schedule, or skip) ---
    if args.send_mode == 'now':
        stage('send')
        sargv = [PY, str(BASE / 'send_drafts.py'),
                 '--file', str(batch), '--count', str(args.count)]
        if args.no_jitter:
            sargv.append('--no-jitter')
        run(sargv, f'send_drafts now x {args.count}')
    elif args.send_mode == 'schedule':
        stage('schedule')
        sargv = [PY, str(BASE / 'send_scheduler.py'),
                 '--wait-and-send',
                 '--file', str(batch), '--count', str(args.count)]
        if args.no_jitter:
            sargv.append('--no-jitter')
        run(sargv, f'scheduled_send (will wait for Germany window) x {args.count}')
    else:
        stage('skip')
        print("Skipping send. Drafts are ready in Outlook for manual review/send.",
              flush=True)

    print("\n[DONE] pipeline complete", flush=True)


if __name__ == '__main__':
    main()
