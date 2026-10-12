"""查询 BotEvent 日志：10-12 拦截时段前后的系统事件。"""
import psycopg2


def load_dsn():
    for line in open(".env"):
        if line.startswith("DATABASE_URL_SYNC"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no sync url")


dsn = load_dsn().replace("postgresql+psycopg2://", "postgresql://")
conn = psycopg2.connect(dsn)
cur = conn.cursor()
# 10-12 07:00-08:00 的 BotEvent
cur.execute(
    """
    SELECT event_type, message, created_at FROM bot_events
    WHERE created_at >= '2026-10-12 06:30' AND created_at <= '2026-10-12 08:00'
    ORDER BY created_at
    """
)
print("=== 10-12 06:30-08:00 BotEvents ===")
for r in cur.fetchall():
    print(r)
# 10-12 全天事件类型统计
cur.execute(
    """
    SELECT event_type, count(*) FROM bot_events
    WHERE created_at >= '2026-10-12 00:00' GROUP BY event_type ORDER BY count(*) DESC
    """
)
print("\n=== 10-12 全天事件类型 ===")
for r in cur.fetchall():
    print(r)
# 10-10 后 MT5/stale/connect 相关
cur.execute(
    """
    SELECT event_type, message, created_at FROM bot_events
    WHERE created_at >= '2026-10-10' AND (
        event_type LIKE '%CIRCUIT%' OR message ILIKE '%stale%' OR message ILIKE '%bridge%'
        OR message ILIKE '%connect%' OR message ILIKE '%health%'
    )
    ORDER BY created_at DESC LIMIT 15
    """
)
print("\n=== 10-10 后 Bridge/健康/熔断 相关事件 ===")
for r in cur.fetchall():
    print(r)
conn.close()