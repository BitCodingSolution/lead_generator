"""
Retry leads that failed with dns_timeout or dns_err — with longer timeout + retry.
"""
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import dns.resolver, dns.exception

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'

_mx_cache = {}

def check_mx(domain, timeout=15.0, attempts=2):
    domain = domain.lower()
    if domain in _mx_cache:
        return _mx_cache[domain]
    last_err = 'unknown'
    for i in range(attempts):
        try:
            r = dns.resolver.Resolver()
            r.lifetime = timeout
            r.timeout = timeout
            r.nameservers = ['8.8.8.8', '1.1.1.1', '9.9.9.9']
            ans = r.resolve(domain, 'MX')
            if len(ans):
                _mx_cache[domain] = (True, '')
                return _mx_cache[domain]
        except dns.resolver.NXDOMAIN:
            _mx_cache[domain] = (False, 'nxdomain'); return _mx_cache[domain]
        except dns.resolver.NoAnswer:
            try:
                r.resolve(domain, 'A')
                _mx_cache[domain] = (True, ''); return _mx_cache[domain]
            except Exception:
                _mx_cache[domain] = (False, 'no_mx_no_a'); return _mx_cache[domain]
        except dns.exception.Timeout:
            last_err = 'dns_timeout'
        except Exception as e:
            last_err = f'dns_err:{type(e).__name__}'
        time.sleep(0.5)
    _mx_cache[domain] = (False, last_err)
    return _mx_cache[domain]

def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT lead_id, email FROM leads
        WHERE email_valid = 0
          AND email_invalid_reason LIKE 'dns_%'
    """).fetchall()
    print(f"Retrying {len(rows):,} timeouts/errors (longer timeout 15s, 2 attempts)...")

    start = time.time()
    results = {}
    def task(r):
        domain = r['email'].split('@')[1]
        ok, reason = check_mx(domain)
        return r['lead_id'], (1 if ok else 0, 'ok' if ok else reason)

    done = 0
    with ThreadPoolExecutor(max_workers=20) as ex:
        fs = [ex.submit(task, r) for r in rows]
        for f in as_completed(fs):
            lid, res = f.result()
            results[lid] = res
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - start
                rate = done / elapsed
                eta = (len(rows) - done) / rate if rate else 0
                print(f"  {done}/{len(rows)} ({rate:.0f}/sec, ETA {eta:.0f}s, {len(_mx_cache)} cached)")

    con.executemany(
        "UPDATE leads SET email_valid=?, email_invalid_reason=?, email_verified_at=CURRENT_TIMESTAMP WHERE lead_id=?",
        [(v[0], v[1], lid) for lid, v in results.items()],
    )
    con.commit()

    recovered = sum(1 for v in results.values() if v[0] == 1)
    still_bad = len(results) - recovered
    print(f"\n[OK] Recovered: {recovered:,}  Still invalid: {still_bad:,}")
    from collections import Counter
    c = Counter(v[1] for v in results.values() if v[0] == 0)
    for r, n in c.most_common():
        print(f"  {n:>6}  {r}")
    con.close()

if __name__ == '__main__':
    main()
