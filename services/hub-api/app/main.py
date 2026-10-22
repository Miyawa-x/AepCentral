"""Elite Hub business service.

Serves the portal under /hub/ and the business API under /hub-api/.
User identity always comes from the server-side session cookie; the frontend
never sends a user_id.
"""
import asyncio
import json
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .compute import IMAGES, SPECS, executor_for

NEWAPI_BASE = os.environ.get("NEWAPI_BASE", "http://127.0.0.1:12221").rstrip("/")
PUBLIC_V1 = os.environ.get("HUB_PUBLIC_V1", "https://xjtuaep.com/hub/v1")
DEMO_ECHO = os.environ.get("HUB_DEMO_ECHO", "0") == "1"
CONTEXT_CHARS = int(os.environ.get("HUB_CONTEXT_CHARS", "24000"))
COOKIE = "elite_hub_session"
PORTAL_DIR = Path(os.environ.get("HUB_PORTAL_DIR", Path(__file__).resolve().parents[3] / "portal"))
DEFAULT_MODELS = ["qwen3.8-flash", "qwen3.8-max", "qwen3.7-plus"]

app = FastAPI(title="Elite Hub API", docs_url="/hub-api/docs", openapi_url="/hub-api/openapi.json")


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------- auth ----------

def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "未登录")
    with db.tx() as c:
        u = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
    if not u:
        raise HTTPException(401, "登录已失效")
    return dict(u)


def admin_user(u=Depends(current_user)):
    if not u["is_admin"]:
        raise HTTPException(403, "需要管理员权限")
    return u


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/hub-api/login")
def login(body: LoginIn, response: Response):
    with db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
        if not u or not db.check_pw(body.password, u["pw_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)", (token, u["id"]))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/",
                        secure=os.environ.get("HUB_COOKIE_SECURE", "1") == "1", max_age=14 * 86400)
    return {"ok": True}


@app.post("/hub-api/logout")
def logout(request: Request, response: Response):
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (request.cookies.get(COOKIE, ""),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/hub-api/me")
def me(u=Depends(current_user)):
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "is_admin": bool(u["is_admin"]), "tier": u["tier"],
            "has_gateway_key": bool(u["gateway_key"]), "compute_minutes": u["compute_minutes"]}


# ---------- models / quota ----------

async def gateway_get(path, key):
    async with httpx.AsyncClient(timeout=10) as cl:
        r = await cl.get(f"{NEWAPI_BASE}{path}", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()


@app.get("/hub-api/models")
async def models(u=Depends(current_user)):
    if u["gateway_key"]:
        try:
            data = await gateway_get("/v1/models", u["gateway_key"])
            ids = sorted(m["id"] for m in data.get("data", []))
            if ids:
                return {"models": ids, "source": "gateway"}
        except Exception:
            pass
    return {"models": DEFAULT_MODELS, "source": "default"}


@app.get("/hub-api/quota")
async def quota(u=Depends(current_user)):
    out = {"base_url": PUBLIC_V1, "key_masked": None, "model_quota": None,
           "compute_minutes": u["compute_minutes"], "tier": u["tier"]}
    k = u["gateway_key"]
    if k:
        out["key_masked"] = k[:6] + "…" + k[-4:]
        try:
            # New API: token usage endpoint, authenticated by the token itself.
            d = await gateway_get("/api/usage/token/", k)
            out["model_quota"] = d.get("data", d)
        except Exception as e:
            out["model_quota_error"] = f"网关额度查询失败：{type(e).__name__}"
    with db.tx() as c:
        out["compute_ledger"] = [dict(r) for r in c.execute(
            "SELECT delta_minutes,reason,instance_id,created_at FROM compute_ledger WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (u["id"],))]
    return out


@app.get("/hub-api/key")
def reveal_key(u=Depends(current_user)):
    if not u["gateway_key"]:
        raise HTTPException(404, "尚未分配个人 Key，请联系管理员")
    return {"key": u["gateway_key"], "base_url": PUBLIC_V1}


# ---------- conversations ----------

def own_conv(c, cid, uid):
    conv = c.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, uid)).fetchone()
    if not conv:
        raise HTTPException(404, "会话不存在")
    return conv


@app.get("/hub-api/conversations")
def list_convs(u=Depends(current_user)):
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,title,model,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC", (u["id"],))]


class ConvIn(BaseModel):
    title: str | None = None
    model: str | None = None


@app.post("/hub-api/conversations")
def create_conv(body: ConvIn, u=Depends(current_user)):
    with db.tx() as c:
        cur = c.execute("INSERT INTO conversations(user_id,title,model) VALUES(?,?,?)",
                        (u["id"], body.title or "新对话", body.model))
        return {"id": cur.lastrowid}


@app.patch("/hub-api/conversations/{cid}")
def rename_conv(cid: int, body: ConvIn, u=Depends(current_user)):
    with db.tx() as c:
        own_conv(c, cid, u["id"])
        if body.title:
            c.execute("UPDATE conversations SET title=? WHERE id=?", (body.title[:80], cid))
        if body.model:
            c.execute("UPDATE conversations SET model=? WHERE id=?", (body.model, cid))
    return {"ok": True}


@app.delete("/hub-api/conversations/{cid}")
def delete_conv(cid: int, u=Depends(current_user)):
    with db.tx() as c:
        own_conv(c, cid, u["id"])
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    return {"ok": True}


@app.get("/hub-api/conversations/{cid}/messages")
def get_messages(cid: int, u=Depends(current_user)):
    with db.tx() as c:
        own_conv(c, cid, u["id"])
        return [dict(r) for r in c.execute(
            "SELECT id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id", (cid,))]


class SendIn(BaseModel):
    content: str | list   # str, or OpenAI multi-part content (text + image_url)
    model: str
    regenerate: bool = False


def build_context(rows):
    """Load context from the current conversation only, newest first within a char budget."""
    msgs, total = [], 0
    for r in reversed(rows):
        content = json.loads(r["content"]) if r["content"].startswith("[") else r["content"]
        size = len(r["content"])
        if msgs and total + size > CONTEXT_CHARS:
            break
        msgs.append({"role": r["role"], "content": content})
        total += size
    return list(reversed(msgs))


@app.post("/hub-api/conversations/{cid}/messages")
async def send(cid: int, body: SendIn, u=Depends(current_user)):
    with db.tx() as c:
        conv = own_conv(c, cid, u["id"])
        if body.regenerate:
            last = c.execute("SELECT id,role FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            if last and last["role"] == "assistant":
                c.execute("DELETE FROM messages WHERE id=?", (last["id"],))
        else:
            stored = body.content if isinstance(body.content, str) else json.dumps(body.content, ensure_ascii=False)
            c.execute("INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)", (cid, "user", stored))
            if conv["title"] == "新对话":
                text = body.content if isinstance(body.content, str) else next(
                    (p.get("text", "") for p in body.content if p.get("type") == "text"), "图片对话")
                c.execute("UPDATE conversations SET title=? WHERE id=?", (text.strip()[:30] or "新对话", cid))
        c.execute("UPDATE conversations SET model=?, updated_at=datetime('now') WHERE id=?", (body.model, cid))
        rows = c.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
    context = build_context(rows)
    key = u["gateway_key"]

    async def stream():
        parts = []
        try:
            if not key:
                if not DEMO_ECHO:
                    yield sse({"error": "尚未分配个人 Key，无法调用模型（请联系管理员）"})
                    return
                text = f"[演示模式 · 未接网关] 收到：{context[-1]['content'] if context else ''}"
                for ch in text:
                    parts.append(ch)
                    yield sse({"delta": ch})
                    await asyncio.sleep(0.01)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as cl:
                    async with cl.stream("POST", f"{NEWAPI_BASE}/v1/chat/completions",
                                         headers={"Authorization": f"Bearer {key}"},
                                         json={"model": body.model, "messages": context, "stream": True}) as r:
                        if r.status_code != 200:
                            err = (await r.aread()).decode(errors="ignore")[:400]
                            yield sse({"error": f"网关返回 {r.status_code}: {err}"})
                            return
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                            except (KeyError, IndexError, json.JSONDecodeError):
                                continue
                            if delta:
                                parts.append(delta)
                                yield sse({"delta": delta})
        finally:
            # Persist whatever was generated, also when the client pressed "stop".
            if parts:
                with db.tx() as c:
                    c.execute("INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)",
                              (cid, "assistant", "".join(parts)))
        yield sse({"done": True})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- Elite Power (compute) ----------

def inst_view(r):
    d = dict(r)
    if d["status"] == "running" and d["started_at"]:
        d["remaining_minutes"] = max(0, math.ceil((parse(d["expires_at"]) - now()).total_seconds() / 60))
    if d["status"] != "running":
        d["ssh_password"] = None
    d["spec_label"] = SPECS.get(d["spec"], {}).get("label", d["spec"])
    return d


@app.get("/hub-api/compute/catalog")
def catalog():
    return {"specs": [{"id": k, **{x: v[x] for x in ("label", "gpu")}} for k, v in SPECS.items()],
            "images": list(IMAGES)}


class ApplyIn(BaseModel):
    spec: str
    image: str = "python"
    minutes: int
    note: str | None = None


@app.post("/hub-api/compute/applications")
def apply(body: ApplyIn, u=Depends(current_user)):
    if body.spec not in SPECS or body.image not in IMAGES:
        raise HTTPException(400, "未知规格或镜像")
    if not 10 <= body.minutes <= 480:
        raise HTTPException(400, "申请时长需在 10–480 分钟")
    simulated = SPECS[body.spec]["gpu"]
    with db.tx() as c:
        if not simulated:
            if c.execute("SELECT COUNT(*) FROM instances WHERE user_id=? AND status IN ('pending','running') AND simulated=0",
                         (u["id"],)).fetchone()[0]:
                raise HTTPException(409, "已有进行中的 CPU 申请或实例")
            # Reserve minutes atomically against the personal balance.
            cur = c.execute("UPDATE users SET compute_minutes=compute_minutes-? WHERE id=? AND compute_minutes>=?",
                            (body.minutes, u["id"], body.minutes))
            if cur.rowcount == 0:
                raise HTTPException(402, "计算时长余额不足")
        cur = c.execute(
            "INSERT INTO instances(user_id,spec,image,status,simulated,requested_minutes,reserved_minutes,note) VALUES(?,?,?,?,?,?,?,?)",
            (u["id"], body.spec, body.image, "pending", int(simulated), body.minutes, 0 if simulated else body.minutes, body.note))
        if not simulated:
            c.execute("INSERT INTO compute_ledger(user_id,instance_id,delta_minutes,reason) VALUES(?,?,?,?)",
                      (u["id"], cur.lastrowid, -body.minutes, "申请预留"))
        return {"id": cur.lastrowid}


@app.get("/hub-api/compute/instances")
def my_instances(u=Depends(current_user)):
    with db.tx() as c:
        return [inst_view(r) for r in c.execute("SELECT * FROM instances WHERE user_id=? ORDER BY id DESC", (u["id"],))]


def finish(c, inst, reason):
    """Stop an instance and settle: charge actual minutes, refund the rest of the reservation."""
    if inst["container_id"]:
        executor_for(inst["spec"]).stop(inst["container_id"])
    used = 0
    if inst["started_at"]:
        used = min(inst["reserved_minutes"], math.ceil((now() - parse(inst["started_at"])).total_seconds() / 60))
    refund = inst["reserved_minutes"] - used
    c.execute("UPDATE instances SET status='stopped', used_minutes=?, note=COALESCE(note,'')||? WHERE id=?",
              (used, f" [{reason}]", inst["id"]))
    if refund:
        c.execute("UPDATE users SET compute_minutes=compute_minutes+? WHERE id=?", (refund, inst["user_id"]))
        c.execute("INSERT INTO compute_ledger(user_id,instance_id,delta_minutes,reason) VALUES(?,?,?,?)",
                  (inst["user_id"], inst["id"], refund, f"{reason}，退回未用时长（实用 {used} 分钟）"))


@app.post("/hub-api/compute/instances/{iid}/stop")
def stop_instance(iid: int, u=Depends(current_user)):
    with db.tx() as c:
        inst = c.execute("SELECT * FROM instances WHERE id=? AND user_id=?", (iid, u["id"])).fetchone()
        if not inst:
            raise HTTPException(404, "实例不存在")
        if inst["status"] not in ("running", "pending"):
            raise HTTPException(409, "实例未在运行")
        finish(c, inst, "用户停止" if inst["status"] == "running" else "用户取消")
    return {"ok": True}


# admin

@app.get("/hub-api/admin/applications")
def admin_list(_=Depends(admin_user)):
    with db.tx() as c:
        return [dict(r) | {"username": r["username"]} for r in c.execute(
            "SELECT i.*, u.username FROM instances i JOIN users u ON u.id=i.user_id ORDER BY i.id DESC LIMIT 100")]


class ReviewIn(BaseModel):
    approve: bool


@app.post("/hub-api/admin/applications/{iid}/review")
def review(iid: int, body: ReviewIn, _=Depends(admin_user)):
    with db.tx() as c:
        # Idempotent: only a pending application can transition.
        claimed = c.execute("UPDATE instances SET status='provisioning' WHERE id=? AND status='pending'", (iid,)).rowcount
        if not claimed:
            raise HTTPException(409, "该申请不是待审批状态")
        inst = c.execute("SELECT * FROM instances WHERE id=?", (iid,)).fetchone()
        if not body.approve:
            finish(c, inst, "审批拒绝")
            c.execute("UPDATE instances SET status='rejected' WHERE id=?", (iid,))
            return {"ok": True}
        used_ports = {r[0] for r in c.execute("SELECT ssh_port FROM instances WHERE status='running' AND ssh_port IS NOT NULL")}
        try:
            info = executor_for(inst["spec"]).create(iid, inst["spec"], inst["image"], used_ports)
        except Exception as e:
            finish(c, inst, "开通失败")
            c.execute("UPDATE instances SET status='failed', note=? WHERE id=?", (str(e)[:300], iid))
            return {"ok": False, "error": str(e)[:300]}
        start = now()
        c.execute("""UPDATE instances SET status='running', started_at=?, expires_at=?, container_id=?,
                     ssh_host=?, ssh_port=?, ssh_user=?, ssh_password=? WHERE id=?""",
                  (iso(start), iso(start + timedelta(minutes=inst["requested_minutes"])), info["container_id"],
                   info["ssh_host"], info["ssh_port"], info["ssh_user"], info["ssh_password"], iid))
    return {"ok": True}


async def expiry_loop():
    while True:
        try:
            with db.tx() as c:
                for inst in c.execute("SELECT * FROM instances WHERE status='running' AND expires_at<=?", (iso(now()),)).fetchall():
                    finish(c, inst, "到期自动停止")
        except Exception as e:  # keep the loop alive
            print("expiry_loop error:", e)
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup():
    db.init()
    asyncio.create_task(expiry_loop())


# ---------- portal ----------

@app.get("/")
def root():
    return RedirectResponse("/hub/")


@app.get("/hub")
def hub_noslash():
    return RedirectResponse("/hub/")


if PORTAL_DIR.exists():
    app.mount("/hub/static", StaticFiles(directory=PORTAL_DIR), name="static")

    @app.get("/hub/{path:path}")
    def spa(path: str):
        # History fallback: every /hub/* route renders the single-page portal.
        return FileResponse(PORTAL_DIR / "index.html")
