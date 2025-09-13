import os
from typing import Any, Dict, List, Optional

import asyncpg

_pool: Optional[asyncpg.pool.Pool] = None


async def init_db_pool(dsn: Optional[str] = None) -> None:
    """Initialize connection pool using provided DSN or DATABASE_URL env."""
    global _pool
    if _pool is None:
        dsn = dsn or os.getenv("DATABASE_URL")
        if dsn is None:
            raise RuntimeError("DATABASE_URL is not configured")
        _pool = await asyncpg.create_pool(dsn)


async def list_users_with_month_stats(ym: str) -> List[asyncpg.Record]:
    """Return users with monthly job stats."""
    assert _pool is not None, "DB pool is not initialized"
    sql = """
        SELECT u.user_id, u.username, u.full_name, u.active, u.last_seen,
               COALESCE(j.jobs_this_month, 0) AS jobs_this_month,
               COALESCE(j.cost_this_month_usd, 0) AS cost_this_month_usd
        FROM users u
        LEFT JOIN (
            SELECT user_id,
                   COUNT(*) AS jobs_this_month,
                   COALESCE(SUM(cost_usd), 0) AS cost_this_month_usd
            FROM jobs
            WHERE to_char(started_at, 'YYYY-MM') = $1
            GROUP BY user_id
        ) j ON j.user_id = u.user_id
        ORDER BY u.user_id
    """
    async with _pool.acquire() as conn:
        return await conn.fetch(sql, ym)


async def block_user(user_id: int, active: bool) -> None:
    """Set active flag for user."""
    assert _pool is not None, "DB pool is not initialized"
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET active=$2 WHERE user_id=$1", user_id, active)


async def insert_user_manual(
    user_id: int, username: Optional[str], full_name: Optional[str]
) -> None:
    """Upsert user created manually."""
    assert _pool is not None, "DB pool is not initialized"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, active)
            VALUES ($1, $2, $3, true)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username,
                full_name = EXCLUDED.full_name
            """,
            user_id,
            username,
            full_name,
        )


async def month_dashboard(ym: str) -> Dict[str, Any]:
    """Return dashboard aggregates for a month."""
    assert _pool is not None, "DB pool is not initialized"
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users_total,
                (SELECT COUNT(*) FROM users WHERE active) AS users_active,
                (SELECT COUNT(*) FROM jobs WHERE to_char(started_at,'YYYY-MM')=$1) AS jobs_month,
                (
                    SELECT COALESCE(SUM(cost_usd),0)
                    FROM jobs WHERE to_char(started_at,'YYYY-MM')=$1
                ) AS cost_month_usd
            """,
            ym,
        )
        return dict(row)


async def list_jobs(ym: Optional[str], user_id: Optional[int]) -> List[asyncpg.Record]:
    """List jobs filtered by month and optionally user_id."""
    assert _pool is not None, "DB pool is not initialized"
    conditions = []
    params: List[Any] = []
    if ym:
        params.append(ym)
        conditions.append(f"to_char(started_at,'YYYY-MM') = ${len(params)}")
    if user_id is not None:
        params.append(user_id)
        conditions.append(f"user_id = ${len(params)}")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT job_id, user_id, filename, status, started_at, finished_at,
               EXTRACT(EPOCH FROM (finished_at - started_at)) AS took_sec,
               tokens_prompt, tokens_completion, cost_usd, model, schema_version
        FROM jobs{where}
        ORDER BY job_id DESC
    """
    async with _pool.acquire() as conn:
        return await conn.fetch(sql, *params)
