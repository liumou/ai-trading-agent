"""对比 #294、#295、#296 三次手动单审查的完整细节。"""
import json
import psycopg2


def load_dsn():
    for line in open("backend/.env"):
        if line.startswith("DATABASE_URL_SYNC"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no sync url")


dsn = load_dsn().replace("postgresql+psycopg2://", "postgresql://")
conn = psycopg2.connect(dsn)
cur = conn.cursor()
for rid in (294, 295, 296, 289):
    cur.execute(
        "SELECT id, symbol, order_type, requested_lot, requested_sl, requested_tp, "
        "expected_price, order_kind, status, error_message, created_at, review "
        "FROM order_audits WHERE id=%s",
        (rid,),
    )
    r = cur.fetchone()
    if not r:
        continue
    print(f"=== #{r[0]} {r[1]} {r[2]} lot={r[3]} SL={r[4]} TP={r[5]} entry={r[6]} "
          f"kind={r[7]} status={r[8]} ===")
    print(f"  created={r[10]}")
    print(f"  error={r[9]}")
    rev = r[11] or {}
    so = rev.get("systemone") or {}
    converge = so.get("converge") or {}
    print(f"  systemone.verdict={converge.get('verdict')} provider={so.get('provider')} "
          f"degraded={so.get('degraded')}")
    for c in (so.get("checks") or []):
        print(f"    {c.get('name')}={c.get('choice')} conf={c.get('confidence')}")
    print()
conn.close()