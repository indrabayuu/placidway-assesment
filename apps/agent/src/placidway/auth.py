import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

COOKIE = "placidway_session"
attempts = defaultdict(deque)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def seed_principals(db):
    config = db.config
    if not config.auth_owner_secret or not config.auth_client_secret:
        raise RuntimeError("Run scripts/bootstrap_env.py to generate login secrets")
    if config.auth_owner_secret == config.auth_client_secret:
        raise RuntimeError("Owner and client login secrets must differ")
    for key, secret, name in (
        ("owner", config.auth_owner_secret, "Owner"),
        ("client", config.auth_client_secret, "PlacidWay"),
    ):
        hashed = token_hash(secret)
        existing = await db.one("SELECT secret_hash FROM principals WHERE id=%s", (key,))
        if existing and not hmac.compare_digest(existing["secret_hash"], hashed):
            await db.execute("DELETE FROM sessions WHERE principal_id=%s", (key,))
        await db.execute(
            "INSERT INTO principals(id,secret_hash,role,name) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET secret_hash=excluded.secret_hash",
            (key, hashed, key, name),
        )


async def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "Please sign in with your access secret")
    user = await request.app.state.db.one(
        "SELECT p.id,p.role,p.name FROM sessions s JOIN principals p ON p.id=s.principal_id WHERE s.token_hash=%s AND s.expires_at>now()",
        (token_hash(token),),
    )
    if not user:
        raise HTTPException(401, "Your session expired. Please sign in again")
    return user


async def login(request: Request, response: Response, secret: str):
    ip = request.client.host if request.client else "local"
    recent = attempts[ip]
    now = time.monotonic()
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= 10:
        raise HTTPException(429, "Too many login attempts. Try again in a minute")
    db = request.app.state.db
    principals = await db.all("SELECT id,role,name,secret_hash FROM principals ORDER BY id")
    given = token_hash(secret)
    matched = None
    for principal in principals:
        if hmac.compare_digest(principal["secret_hash"], given):
            matched = principal
    if not matched:
        recent.append(now)
        raise HTTPException(401, "Invalid access secret")
    recent.clear()
    previous = request.cookies.get(COOKIE)
    if previous:
        await db.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash(previous),))
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(days=db.config.session_days)
    await db.execute(
        "INSERT INTO sessions(token_hash,principal_id,expires_at) VALUES(%s,%s,%s)",
        (token_hash(token), matched["id"], expiry),
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=db.config.cookie_secure,
        samesite="lax",
        max_age=db.config.session_days * 86400,
        path="/api",
    )
    return {k: matched[k] for k in ("id", "role", "name")}
