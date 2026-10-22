// Elite Hub portal — single page, no build step. All identity comes from the server session cookie.
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let ME = null;

async function api(path, opts = {}) {
  const r = await fetch("/hub-api" + path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) { ME = null; renderLogin(); throw new Error("401"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function go(path) { history.pushState({}, "", path); route(); }
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (a) { e.preventDefault(); go(a.getAttribute("href")); }
});
window.addEventListener("popstate", route);

async function route() {
  const p = location.pathname.replace(/^\/hub/, "") || "/";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "/hub" + p));
  if (!ME) {
    try { ME = await api("/me"); } catch { return; }
  }
  $("#who").innerHTML = `${esc(ME.display_name)} · ${ME.tier === "elite" ? "越杰" : "校园"} · <a href="#" id="logout">退出</a>`;
  $("#logout").onclick = async (e) => { e.preventDefault(); await api("/logout", { method: "POST" }); ME = null; renderLogin(); };
  $("#nav-admin").hidden = !ME.is_admin;
  if (p.startsWith("/chat")) return renderChat();
  if (p.startsWith("/api")) return renderApi();
  if (p.startsWith("/compute")) return renderCompute();
  if (p.startsWith("/admin")) return renderAdmin();
  renderHome();
}

function renderLogin() {
  $("#who").textContent = "";
  view.innerHTML = `<div class="card login"><h1>登录 Elite Hub</h1>
    <p class="muted">公共介绍开放浏览；对话、个人 Key 与算力需登录使用。</p>
    <form id="lf"><label>用户名</label><input name="u" autocomplete="username" required>
    <label>密码</label><input name="p" type="password" autocomplete="current-password" required>
    <p class="err" id="lerr"></p><button>登录</button></form></div>`;
  $("#lf").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/login", { method: "POST", body: { username: e.target.u.value, password: e.target.p.value } });
      ME = null; route();
    } catch (err) { $("#lerr").textContent = err.message; }
  };
}

function renderHome() {
  view.innerHTML = `<h1>一个入口，两类资源</h1>
  <p class="muted">Elite Hub 统一提供 AI 模型与计算环境，降低同学们尝试智能应用、开展科研实验的起步成本。模型用量与计算时长分别记账。</p>
  <div class="grid">
    <a class="card" href="/hub/chat/" data-link><h3>AI 对话</h3><p class="muted">连续对话、图片理解；会话按个人保存，刷新不丢。</p></a>
    <a class="card" href="/hub/api/" data-link><h3>API 与额度</h3><p class="muted">个人 Key、可用模型、剩余额度与使用记录。</p></a>
    <a class="card" href="/hub/compute/" data-link><h3>Elite Power</h3><p class="muted">申请 CPU 环境，通过 SSH 使用；到期自动回收。</p></a>
  </div>
  <h2>资源规则</h2>
  <div class="card"><ul>
    <li>资源面向全校同学开放，越杰同学享更高额度。</li>
    <li>模型费用按网关计量扣减个人额度；计算时长申请时预留、按实际运行分钟扣减、停止或到期后退回剩余。</li>
    <li>GPU 资源目前为<b>模拟流程</b>，获得预算后接入真实云 GPU。</li>
    <li>资助资格与重要规则由人工审核，AI 仅作辅助。</li></ul></div>`;
}

// ---------------- chat ----------------
let chat = { convs: [], cur: null, models: [], abort: null, image: null };

async function renderChat() {
  view.innerHTML = `<div class="chat">
    <aside class="side"><button id="newc" style="width:100%;margin-bottom:10px">＋ 新对话</button><div id="clist"></div></aside>
    <section class="pane">
      <div class="bar"><select id="model"></select><span class="muted" id="ctitle"></span></div>
      <div class="msgs" id="msgs"><p class="muted">选择或新建一个对话。</p></div>
      <div class="composer">
        <div style="flex:1"><textarea id="input" placeholder="输入问题，Enter 发送，Shift+Enter 换行"></textarea>
          <div class="attach"><label style="display:inline;cursor:pointer">📎 图片<input type="file" id="img" accept="image/*" hidden></label> <span id="imgname"></span></div></div>
        <div style="display:flex;flex-direction:column;gap:6px">
          <button id="send">发送</button><button class="ghost" id="stop" hidden>停止</button><button class="ghost" id="regen">重新生成</button></div>
      </div></section></div>`;
  const m = await api("/models");
  chat.models = m.models;
  $("#model").innerHTML = m.models.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#newc").onclick = newConv;
  $("#send").onclick = () => send(false);
  $("#regen").onclick = () => send(true);
  $("#stop").onclick = () => chat.abort?.abort();
  $("#input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(false); } };
  $("#img").onchange = (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 4 * 1024 * 1024) { alert("图片需小于 4MB"); return; }
    const rd = new FileReader(); rd.onload = () => { chat.image = rd.result; $("#imgname").textContent = f.name + " ✕"; }; rd.readAsDataURL(f);
  };
  $("#imgname").onclick = () => { chat.image = null; $("#imgname").textContent = ""; };
  await loadConvs();
  if (chat.cur && chat.convs.some((c) => c.id === chat.cur)) openConv(chat.cur);
}

async function loadConvs() {
  chat.convs = await api("/conversations");
  $("#clist").innerHTML = chat.convs.map((c) => `<div class="conv ${c.id === chat.cur ? "on" : ""}" data-id="${c.id}">
    <span>${esc(c.title)}</span><span class="ops"><a data-op="ren">改名</a> <a data-op="del">删除</a></span></div>`).join("") || `<p class="muted">暂无对话</p>`;
  $("#clist").querySelectorAll(".conv").forEach((el) => {
    const id = +el.dataset.id;
    el.onclick = async (e) => {
      const op = e.target.dataset.op;
      if (op === "del") { if (confirm("删除该对话？")) { await api(`/conversations/${id}`, { method: "DELETE" }); if (chat.cur === id) chat.cur = null; loadConvs(); $("#msgs").innerHTML = ""; } return; }
      if (op === "ren") { const t = prompt("新名称"); if (t) { await api(`/conversations/${id}`, { method: "PATCH", body: { title: t } }); loadConvs(); } return; }
      openConv(id);
    };
  });
}

async function newConv() {
  const { id } = await api("/conversations", { method: "POST", body: { model: $("#model").value } });
  chat.cur = id; await loadConvs(); openConv(id);
}

function renderContent(c) {
  if (typeof c === "string" && c.startsWith("[")) { try { c = JSON.parse(c); } catch {} }
  if (Array.isArray(c)) return c.map((p) => p.type === "text" ? esc(p.text) : `<img src="${esc(p.image_url?.url)}">`).join("");
  return esc(c);
}

async function openConv(id) {
  chat.cur = id;
  document.querySelectorAll(".conv").forEach((el) => el.classList.toggle("on", +el.dataset.id === id));
  const conv = chat.convs.find((c) => c.id === id);
  $("#ctitle").textContent = conv?.title || "";
  if (conv?.model && chat.models.includes(conv.model)) $("#model").value = conv.model;
  const msgs = await api(`/conversations/${id}/messages`);
  $("#msgs").innerHTML = msgs.map((m) => `<div class="msg ${m.role}">${renderContent(m.content)}</div>`).join("") || `<p class="muted">开始提问吧。</p>`;
  $("#msgs").scrollTop = 1e9;
}

async function send(regenerate) {
  if (chat.abort) return;
  if (!chat.cur) await newConv();
  const text = $("#input").value.trim();
  if (!regenerate && !text && !chat.image) return;
  let content = text;
  if (!regenerate && chat.image) content = [{ type: "text", text: text || "请描述这张图片" }, { type: "image_url", image_url: { url: chat.image } }];
  const box = $("#msgs");
  if (box.querySelector("p.muted")) box.innerHTML = "";
  if (regenerate) { const last = box.lastElementChild; if (last?.classList.contains("assistant")) last.remove(); }
  else box.insertAdjacentHTML("beforeend", `<div class="msg user">${renderContent(content)}</div>`);
  $("#input").value = ""; chat.image = null; $("#imgname").textContent = "";
  const out = document.createElement("div"); out.className = "msg assistant"; out.textContent = "…"; box.appendChild(out);
  box.scrollTop = 1e9;
  chat.abort = new AbortController();
  $("#stop").hidden = false; $("#send").disabled = true;
  let acc = "";
  try {
    const r = await fetch(`/hub-api/conversations/${chat.cur}/messages`, {
      method: "POST", credentials: "same-origin", signal: chat.abort.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: regenerate ? "" : content, model: $("#model").value, regenerate }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.delta) { acc += ev.delta; out.textContent = acc; box.scrollTop = 1e9; }
        if (ev.error) { out.textContent = "⚠ " + ev.error; }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") out.textContent = "⚠ " + e.message;
    else out.textContent = acc + " [已停止]";
  } finally {
    chat.abort = null; $("#stop").hidden = true; $("#send").disabled = false; loadConvs();
  }
}

// ---------------- API & quota ----------------
async function renderApi() {
  view.innerHTML = `<h1>API 与额度</h1><p class="muted">加载中…</p>`;
  const [q, m] = await Promise.all([api("/quota"), api("/models")]);
  const mq = q.model_quota;
  const fmt = (v) => (v == null ? "—" : (typeof v === "number" ? (v / 500000).toFixed(2) + " 元" : esc(v)));
  view.innerHTML = `<h1>API 与额度</h1>
  <div class="grid">
    <div class="card"><h3>模型额度</h3>${mq ? `<p>已用 <b>${fmt(mq.total_used)}</b> · 剩余 <b>${mq.unlimited_quota ? "不限" : fmt(mq.total_available)}</b></p>` : `<p class="muted">${esc(q.model_quota_error || "尚未分配个人 Key")}</p>`}</div>
    <div class="card"><h3>计算时长</h3><p>剩余 <b>${q.compute_minutes}</b> 分钟</p><p class="muted">${q.tier === "elite" ? "越杰同学 · 高峰期优先" : "校园同学"}</p></div>
    <div class="card"><h3>个人 Key</h3>${q.key_masked ? `<p><code id="k">${esc(q.key_masked)}</code></p><button class="ghost" id="reveal">显示 / 复制</button>` : `<p class="muted">未分配，请联系管理员</p>`}</div>
  </div>
  <h2>快速开始</h2>
  <pre>export ELITE_HUB_BASE_URL=${esc(q.base_url)}
export ELITE_HUB_API_KEY=&lt;你的个人Key&gt;

curl $ELITE_HUB_BASE_URL/chat/completions \\
  -H "Authorization: Bearer $ELITE_HUB_API_KEY" -H "Content-Type: application/json" \\
  -d '{"model":"${esc(m.models[0])}","messages":[{"role":"user","content":"你好"}]}'</pre>
  <pre>from openai import OpenAI
import os
client = OpenAI(base_url=os.environ["ELITE_HUB_BASE_URL"], api_key=os.environ["ELITE_HUB_API_KEY"])
print(client.chat.completions.create(model="${esc(m.models[0])}", messages=[{"role": "user", "content": "你好"}]).choices[0].message.content)</pre>
  <h2>可用模型 <span class="tag">${m.source === "gateway" ? "来自网关" : "默认目录"}</span></h2>
  <div class="card">${m.models.map((x) => `<code>${esc(x)}</code>`).join(" · ")}</div>
  <h2>计算时长流水</h2>
  <div class="card"><table><tr><th>时间 (UTC)</th><th>变动</th><th>说明</th></tr>
  ${q.compute_ledger.map((l) => `<tr><td>${esc(l.created_at)}</td><td>${l.delta_minutes > 0 ? "+" : ""}${l.delta_minutes} 分钟</td><td>${esc(l.reason)}${l.instance_id ? ` (#${l.instance_id})` : ""}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">暂无记录</td></tr>`}</table></div>`;
  const rv = $("#reveal");
  if (rv) rv.onclick = async () => { const { key } = await api("/key"); $("#k").textContent = key; navigator.clipboard?.writeText(key); rv.textContent = "已复制"; };
}

// ---------------- compute ----------------
const STATUS = { pending: ["待审批", "warn"], provisioning: ["开通中", "warn"], running: ["运行中", "ok"], stopped: ["已停止", ""], rejected: ["已拒绝", ""], failed: ["开通失败", ""] };

async function renderCompute() {
  const [cat, list] = await Promise.all([api("/compute/catalog"), api("/compute/instances")]);
  const me = await api("/me");
  view.innerHTML = `<h1>Elite Power</h1>
  <p class="muted">剩余计算时长 <b>${me.compute_minutes}</b> 分钟。申请时预留，运行按分钟扣减，停止 / 到期后退回剩余。</p>
  <div class="grid">${cat.specs.map((s) => `<div class="card"><h3>${esc(s.label)}</h3>
    ${s.gpu ? `<span class="tag warn">模拟流程 · 非真实 GPU</span>` : `<span class="tag ok">真实 CPU 容器</span>`}
    <p class="muted">${s.gpu ? "演示排队与开通流程，不消耗时长" : "Python 环境 · SSH 登录"}</p>
    <button data-spec="${s.id}">申请</button></div>`).join("")}</div>
  <div class="card" id="form" hidden style="margin-top:16px"><h3 id="ftitle"></h3>
    <label>镜像</label><select id="fimg">${cat.images.map((i) => `<option>${esc(i)}</option>`).join("")}</select>
    <label>使用时长（分钟，10–480）</label><input id="fmin" type="number" value="60" min="10" max="480">
    <label>用途说明</label><input id="fnote" placeholder="如：课程实验 / 数据处理">
    <p class="err" id="ferr"></p><button id="fsub">提交申请</button></div>
  <h2>我的实例</h2>
  <div class="card"><table><tr><th>#</th><th>规格</th><th>状态</th><th>连接</th><th>时长</th><th></th></tr>
  ${list.map((i) => {
    const [label, cls] = STATUS[i.status] || [i.status, ""];
    const ssh = i.status === "running" ? `<code>ssh ${esc(i.ssh_user)}@${esc(i.ssh_host)} -p ${i.ssh_port}</code><br>密码 <code>${esc(i.ssh_password)}</code>` : "—";
    const time = i.status === "running" ? `剩余 ${i.remaining_minutes ?? "—"} 分钟` : i.status === "stopped" ? `实用 ${i.used_minutes} 分钟` : `申请 ${i.requested_minutes} 分钟`;
    return `<tr><td>${i.id}</td><td>${esc(i.spec_label)}${i.simulated ? ' <span class="tag warn">模拟</span>' : ""}</td>
      <td><span class="tag ${cls}">${label}</span></td><td>${ssh}</td><td>${time}</td>
      <td>${["running", "pending"].includes(i.status) ? `<button class="ghost" data-stop="${i.id}">${i.status === "running" ? "停止" : "取消"}</button>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">暂无实例</td></tr>`}</table></div>`;
  let spec = null;
  view.querySelectorAll("[data-spec]").forEach((b) => b.onclick = () => {
    spec = b.dataset.spec; $("#form").hidden = false; $("#ftitle").textContent = "申请：" + cat.specs.find((s) => s.id === spec).label;
  });
  $("#fsub").onclick = async () => {
    try { await api("/compute/applications", { method: "POST", body: { spec, image: $("#fimg").value, minutes: +$("#fmin").value, note: $("#fnote").value } }); renderCompute(); }
    catch (e) { $("#ferr").textContent = e.message; }
  };
  view.querySelectorAll("[data-stop]").forEach((b) => b.onclick = async () => {
    if (confirm("确定停止？停止后退回未使用时长。")) { await api(`/compute/instances/${b.dataset.stop}/stop`, { method: "POST" }); renderCompute(); }
  });
}

// ---------------- admin ----------------
async function renderAdmin() {
  const list = await api("/admin/applications");
  view.innerHTML = `<h1>算力申请审批</h1><div class="card"><table><tr><th>#</th><th>用户</th><th>规格</th><th>时长</th><th>说明</th><th>状态</th><th></th></tr>
  ${list.map((i) => `<tr><td>${i.id}</td><td>${esc(i.username)}</td><td>${esc(i.spec)}${i.simulated ? " (模拟)" : ""}</td><td>${i.requested_minutes}</td>
    <td>${esc(i.note)}</td><td>${esc((STATUS[i.status] || [i.status])[0])}</td>
    <td>${i.status === "pending" ? `<button data-ok="${i.id}">通过</button> <button class="ghost" data-no="${i.id}">拒绝</button>` : ""}</td></tr>`).join("")}</table></div>`;
  const act = async (id, approve) => { const r = await api(`/admin/applications/${id}/review`, { method: "POST", body: { approve } }); if (r.error) alert(r.error); renderAdmin(); };
  view.querySelectorAll("[data-ok]").forEach((b) => b.onclick = () => act(b.dataset.ok, true));
  view.querySelectorAll("[data-no]").forEach((b) => b.onclick = () => act(b.dataset.no, false));
}

route();
