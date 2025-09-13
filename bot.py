import base64
import csv
import logging
import os
from datetime import datetime
from io import StringIO
from typing import Optional

from aiohttp import web

import db

log = logging.getLogger(__name__)

ADMIN_BASIC_USER = os.getenv("ADMIN_BASIC_USER")
ADMIN_BASIC_PASS = os.getenv("ADMIN_BASIC_PASS")


async def require_admin(request: web.Request) -> None:
    if not ADMIN_BASIC_USER or not ADMIN_BASIC_PASS:
        raise web.HTTPNotFound()
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Basic "):
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="ttn-bot admin"'})
    try:
        decoded = base64.b64decode(auth[6:]).decode()
    except Exception:  # pragma: no cover - malformed base64
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="ttn-bot admin"'})
    username, _, password = decoded.partition(":")
    if username != ADMIN_BASIC_USER or password != ADMIN_BASIC_PASS:
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="ttn-bot admin"'})


def render_page(title: str, body: str) -> web.Response:
    nav = '<nav><a href="/admin">Dashboard</a> | <a href="/admin/users">Users</a> | <a href="/admin/jobs">Jobs</a></nav>'
    html = f"""
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{ font-family: sans-serif; margin: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; }}
            th {{ background: #f4f4f4; }}
            nav a {{ margin-right: 10px; }}
        </style>
    </head>
    <body>
        {nav}
        <h1>{title}</h1>
        {body}
    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html")


async def admin_dashboard(request: web.Request) -> web.Response:
    await require_admin(request)
    ym = datetime.utcnow().strftime("%Y-%m")
    stats = await db.month_dashboard(ym)
    body = f"""
    <p>Month: {ym}</p>
    <ul>
        <li>Users total: {stats['users_total']}</li>
        <li>Users active: {stats['users_active']}</li>
        <li>Jobs this month: {stats['jobs_month']}</li>
        <li>Cost this month USD: {stats['cost_month_usd']}</li>
    </ul>
    <p><a href="/admin/users">Users</a> | <a href="/admin/jobs">Jobs</a></p>
    """
    log.info("admin dashboard viewed")
    return render_page("Admin Dashboard", body)


async def admin_users(request: web.Request) -> web.Response:
    await require_admin(request)
    ym = request.query.get("ym") or datetime.utcnow().strftime("%Y-%m")
    users = await db.list_users_with_month_stats(ym)
    rows = "".join(
        f"<tr><td>{u['user_id']}</td><td>{u['username'] or ''}</td><td>{u['full_name'] or ''}</td>"
        f"<td>{'yes' if u['active'] else 'no'}</td><td>{u['last_seen'] or ''}</td>"
        f"<td>{u['jobs_this_month']}</td><td>{u['cost_this_month_usd']}</td>"
        f"<td>"
        f"<form method='post' action='/admin/users/{'block' if u['active'] else 'unblock'}'>"
        f"<input type='hidden' name='user_id' value='{u['user_id']}'/>"
        f"<button type='submit'>{'Block' if u['active'] else 'Unblock'}</button>"
        f"</form></td></tr>"
        for u in users
    )
    body = f"""
    <form method="get">
        Month: <input type="text" name="ym" value="{ym}"/>
        <button type="submit">Filter</button>
    </form>
    <h2>Add user</h2>
    <form method="post" action="/admin/users/add">
        ID: <input type="number" name="user_id" required/>
        Username: <input type="text" name="username"/>
        Full name: <input type="text" name="full_name"/>
        <button type="submit">Save</button>
    </form>
    <h2>Users</h2>
    <table>
        <tr><th>ID</th><th>Username</th><th>Full name</th><th>Active</th><th>Last seen</th><th>Jobs</th><th>Cost USD</th><th>Action</th></tr>
        {rows}
    </table>
    """
    log.info("admin users viewed")
    return render_page("Users", body)


async def admin_users_add(request: web.Request) -> web.Response:
    await require_admin(request)
    form = await request.post()
    user_id = int(form["user_id"])
    username = form.get("username") or None
    full_name = form.get("full_name") or None
    await db.insert_user_manual(user_id, username, full_name)
    log.info("user %s added/updated", user_id)
    raise web.HTTPFound("/admin/users")


async def admin_users_block(request: web.Request) -> web.Response:
    await require_admin(request)
    form = await request.post()
    user_id = int(form["user_id"])
    await db.block_user(user_id, False)
    log.info("user %s blocked", user_id)
    raise web.HTTPFound("/admin/users")


async def admin_users_unblock(request: web.Request) -> web.Response:
    await require_admin(request)
    form = await request.post()
    user_id = int(form["user_id"])
    await db.block_user(user_id, True)
    log.info("user %s unblocked", user_id)
    raise web.HTTPFound("/admin/users")


async def admin_jobs(request: web.Request) -> web.Response:
    await require_admin(request)
    ym = request.query.get("ym") or datetime.utcnow().strftime("%Y-%m")
    user_id = request.query.get("user_id")
    user_id_int: Optional[int] = int(user_id) if user_id else None
    jobs = await db.list_jobs(ym, user_id_int)
    rows = "".join(
        f"<tr><td>{j['job_id']}</td><td>{j['user_id']}</td><td>{j['filename'] or ''}</td>"
        f"<td>{j['status']}</td><td>{j['started_at']}</td><td>{j['finished_at'] or ''}</td>"
        f"<td>{j['took_sec'] or ''}</td><td>{j['tokens_prompt'] or ''}</td>"
        f"<td>{j['tokens_completion'] or ''}</td><td>{j['cost_usd'] or ''}</td>"
        f"<td>{j['model']}</td><td>{j['schema_version']}</td></tr>"
        for j in jobs
    )
    csv_link = f"/admin/jobs.csv?ym={ym}" + (f"&user_id={user_id_int}" if user_id_int else "")
    body = f"""
    <form method="get">
        Month: <input type="text" name="ym" value="{ym}"/>
        User ID: <input type="text" name="user_id" value="{user_id or ''}"/>
        <button type="submit">Filter</button>
    </form>
    <p><a href="{csv_link}">Export CSV</a></p>
    <table>
        <tr><th>ID</th><th>User</th><th>Filename</th><th>Status</th><th>Started</th><th>Finished</th><th>Took</th><th>Prompt</th><th>Completion</th><th>Cost USD</th><th>Model</th><th>Schema</th></tr>
        {rows}
    </table>
    """
    log.info("admin jobs viewed")
    return render_page("Jobs", body)


async def admin_jobs_csv(request: web.Request) -> web.Response:
    await require_admin(request)
    ym = request.query.get("ym") or datetime.utcnow().strftime("%Y-%m")
    user_id = request.query.get("user_id")
    user_id_int: Optional[int] = int(user_id) if user_id else None
    jobs = await db.list_jobs(ym, user_id_int)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "job_id",
        "user_id",
        "filename",
        "status",
        "started_at",
        "finished_at",
        "took_sec",
        "tokens_prompt",
        "tokens_completion",
        "cost_usd",
        "model",
        "schema_version",
    ])
    for j in jobs:
        writer.writerow([
            j["job_id"],
            j["user_id"],
            j["filename"],
            j["status"],
            j["started_at"],
            j["finished_at"],
            j["took_sec"],
            j["tokens_prompt"],
            j["tokens_completion"],
            j["cost_usd"],
            j["model"],
            j["schema_version"],
        ])
    csv_data = buffer.getvalue()
    filename = f"jobs-{ym}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    log.info("admin jobs csv exported")
    return web.Response(text=csv_data, content_type="text/csv", headers=headers)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def webhook(request: web.Request) -> web.Response:
    data = await request.json()
    return web.json_response({"ok": True, "received": data})


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/health", health),
        web.post("/webhook", webhook),
        web.get("/admin", admin_dashboard),
        web.get("/admin/users", admin_users),
        web.post("/admin/users/add", admin_users_add),
        web.post("/admin/users/block", admin_users_block),
        web.post("/admin/users/unblock", admin_users_unblock),
        web.get("/admin/jobs", admin_jobs),
        web.get("/admin/jobs.csv", admin_jobs_csv),
    ])
    return app


if __name__ == "__main__":
    web.run_app(create_app())
