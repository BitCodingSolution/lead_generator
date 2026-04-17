"""
Free email verification: syntax + MX + disposable + role-based.

Marks leads as email_valid=0 in DB and excludes them from future picks.
Soft delete only — rows stay for audit, but pick_batch won't see them.

Usage:
    python verify_emails_free.py            (full run, all leads)
    python verify_emails_free.py --limit 100 (test on 100)
    python verify_emails_free.py --workers 30  (parallel DNS lookups)
"""
import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import dns.resolver
import dns.exception

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'

# Disposable/temporary email providers — common list
DISPOSABLE = {
    'mailinator.com', 'tempmail.com', 'temp-mail.org', '10minutemail.com',
    'guerrillamail.com', 'yopmail.com', 'throwaway.email', 'trashmail.com',
    'sharklasers.com', 'maildrop.cc', 'fakeinbox.com', 'dispostable.com',
    'getairmail.com', 'emailondeck.com', 'spamgourmet.com', 'mytrashmail.com',
}

# Role/shared inbox prefixes (not decision-makers)
ROLE_PREFIXES = {
    'info', 'office', 'kontakt', 'admin', 'support', 'sales', 'noreply',
    'no-reply', 'hello', 'hallo', 'service', 'webmaster', 'postmaster',
    'buchhaltung', 'rechnung', 'marketing', 'hr', 'personal', 'jobs',
    'career', 'karriere', 'mail', 'newsletter', 'press', 'presse',
}

EMAIL_RE = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def check_syntax(email: str) -> tuple[bool, str]:
    if not email or '@' not in email:
        return False, 'missing_at'
    if not EMAIL_RE.match(email):
        return False, 'bad_syntax'
    local, _, domain = email.partition('@')
    if len(local) > 64 or len(domain) > 255 or len(email) > 254:
        return False, 'too_long'
    if '..' in email:
        return False, 'double_dot'
    return True, ''


def check_role(email: str) -> tuple[bool, str]:
    local = email.split('@')[0].lower()
    if local in ROLE_PREFIXES:
        return False, f'role_prefix:{local}'
    return True, ''


def check_disposable(email: str) -> tuple[bool, str]:
    domain = email.split('@')[-1].lower()
    if domain in DISPOSABLE:
        return False, 'disposable'
    return True, ''


_mx_cache: dict[str, tuple[bool, str]] = {}


def check_mx(domain: str, timeout: float = 5.0) -> tuple[bool, str]:
    domain = domain.lower()
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        resolver.timeout = timeout
        resolver.nameservers = ['8.8.8.8', '1.1.1.1']
        answers = resolver.resolve(domain, 'MX')
        ok = len(answers) > 0
        result = (ok, '' if ok else 'no_mx')
    except dns.resolver.NXDOMAIN:
        result = (False, 'nxdomain')
    except dns.resolver.NoAnswer:
        # try A record fallback
        try:
            resolver.resolve(domain, 'A')
            result = (True, '')  # has A record, fallback ok
        except Exception:
            result = (False, 'no_mx_no_a')
    except dns.exception.Timeout:
        result = (False, 'dns_timeout')
    except Exception as e:
        result = (False, f'dns_err:{type(e).__name__}')
    _mx_cache[domain] = result
    return result


def verify(email: str) -> tuple[int, str]:
    if not email:
        return 0, 'empty'
    email = email.strip().lower()

    ok, reason = check_syntax(email)
    if not ok:
        return 0, reason

    ok, reason = check_role(email)
    if not ok:
        return 0, reason

    ok, reason = check_disposable(email)
    if not ok:
        return 0, reason

    domain = email.split('@')[1]
    ok, reason = check_mx(domain)
    if not ok:
        return 0, reason

    return 1, 'ok'


def ensure_columns(con):
    cur = con.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(leads)").fetchall()}
    if 'email_valid' not in cols:
        cur.execute("ALTER TABLE leads ADD COLUMN email_valid INTEGER")
    if 'email_invalid_reason' not in cols:
        cur.execute("ALTER TABLE leads ADD COLUMN email_invalid_reason TEXT")
    if 'email_verified_at' not in cols:
        cur.execute("ALTER TABLE leads ADD COLUMN email_verified_at TIMESTAMP")
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--workers', type=int, default=30)
    ap.add_argument('--recheck', action='store_true', help='Re-verify even if already checked')
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_columns(con)

    where = "" if args.recheck else "WHERE email_valid IS NULL"
    q = f"SELECT lead_id, email FROM leads {where} ORDER BY lead_id"
    if args.limit:
        q += f" LIMIT {args.limit}"
    rows = con.execute(q).fetchall()
    print(f"Verifying {len(rows):,} leads (workers={args.workers})...")
    start = time.time()

    results: dict[str, tuple[int, str]] = {}
    # First pass: syntax / role / disposable (fast, serial)
    needs_mx: list[tuple[str, str]] = []
    for r in rows:
        lead_id = r['lead_id']
        email = (r['email'] or '').strip().lower()
        if not email:
            results[lead_id] = (0, 'empty')
            continue
        ok, reason = check_syntax(email)
        if not ok:
            results[lead_id] = (0, reason); continue
        ok, reason = check_role(email)
        if not ok:
            results[lead_id] = (0, reason); continue
        ok, reason = check_disposable(email)
        if not ok:
            results[lead_id] = (0, reason); continue
        needs_mx.append((lead_id, email))

    print(f"  Fast rejects: {len(results):,}")
    print(f"  MX to check:  {len(needs_mx):,}")

    # Second pass: MX (parallel, cached by domain)
    done = 0
    if needs_mx:
        def _mx_task(item):
            lead_id, email = item
            domain = email.split('@')[1]
            ok, reason = check_mx(domain)
            return lead_id, (1, 'ok') if ok else (0, reason)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_mx_task, it) for it in needs_mx]
            for f in as_completed(futures):
                lid, res = f.result()
                results[lid] = res
                done += 1
                if done % 1000 == 0:
                    elapsed = time.time() - start
                    rate = done / elapsed
                    eta = (len(needs_mx) - done) / rate if rate else 0
                    print(f"  MX checked: {done:,}/{len(needs_mx):,} "
                          f"({rate:.0f}/sec, ETA {eta:.0f}s, "
                          f"{len(_mx_cache):,} unique domains cached)")

    # Write results
    print("\nWriting results to DB...")
    con.executemany(
        "UPDATE leads SET email_valid=?, email_invalid_reason=?, "
        "email_verified_at=CURRENT_TIMESTAMP WHERE lead_id=?",
        [(v[0], v[1], lid) for lid, v in results.items()],
    )
    con.commit()

    # Summary
    valid = sum(1 for v in results.values() if v[0] == 1)
    invalid = len(results) - valid
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Verified: {len(results):,} in {elapsed:.1f}s")
    print(f"  Valid:    {valid:,}")
    print(f"  Invalid:  {invalid:,}")
    print(f"  Unique domains cached: {len(_mx_cache):,}")

    print(f"\nInvalid reason breakdown:")
    from collections import Counter
    reasons = Counter(v[1] for v in results.values() if v[0] == 0)
    for reason, count in reasons.most_common():
        print(f"  {count:>6,}  {reason}")
    print('='*60)

    con.close()


if __name__ == '__main__':
    main()
