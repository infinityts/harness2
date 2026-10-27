WAR_ROOM_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harness War Room</title>
<style>
  :root { --bg:#0b1020; --panel:#121a2e; --line:#24314f; --text:#dbe6ff; --muted:#8ea2c9; --ok:#34d399; --warn:#fbbf24; --bad:#f87171; --accent:#60a5fa; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.35 "Segoe UI",system-ui,sans-serif; }
  header { display:flex; gap:10px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--line); background:var(--panel); position:sticky; top:0; z-index:5; }
  header b { color:var(--accent); letter-spacing:.08em; }
  input,select,textarea,button { background:#0d1425; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:6px 8px; font:inherit; }
  input[type=password] { width:260px; }
  button { cursor:pointer; background:#1b2a48; }
  button:hover { border-color:var(--accent); }
  main { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(340px,.9fr); gap:12px; padding:12px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px; }
  .card h3 { margin:0 0 8px; font-size:13px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; }
  #graph { width:100%; height:560px; display:block; }
  .row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-bottom:8px; }
  .row > * { flex:1 1 auto; min-width:0; }
  .row button { flex:0 0 auto; }
  #events { height:300px; overflow:auto; background:#080d19; border:1px solid var(--line); border-radius:8px; padding:8px; font:12px/1.4 ui-monospace,Consolas,monospace; white-space:pre-wrap; }
  .ev { border-bottom:1px dashed #1b2742; padding:3px 0; }
  .ev b { color:var(--accent); }
  .locks { max-height:140px; overflow:auto; font:12px ui-monospace,Consolas,monospace; }
  .muted { color:var(--muted); }
  .pill { display:inline-block; padding:1px 6px; border-radius:999px; border:1px solid var(--line); font-size:11px; }
  @media (max-width:900px) { main { grid-template-columns:1fr; } #graph { height:420px; } }
</style>
</head>
<body>
<header>
  <b>HARNESS WAR ROOM</b>
  <input id="key" type="password" placeholder="X-Harness-Admin-Key" autocomplete="off">
  <button id="connect">Conectar</button>
  <span id="status" class="muted">desconectado</span>
</header>
<main>
  <section class="card">
    <h3>Topologia en vivo</h3>
    <svg id="graph" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid meet"></svg>
  </section>
  <aside style="display:flex;flex-direction:column;gap:12px;min-width:0">
    <div class="card">
      <h3>Cables / enlaces</h3>
      <div class="row"><select id="lsrc"></select><select id="ltgt"></select></div>
      <div class="row">
        <select id="ltype"><option>PIPELINE_HANDOFF</option><option>PEER_REVIEW</option><option>CONTEXT_RELAY</option><option>SUPERVISOR</option></select>
        <select id="ltrig"><option>ON_TASK_MERGED</option><option>ON_CONFLICT</option><option>MANUAL_RELAY</option></select>
        <button id="link">Crear enlace</button>
      </div>
    </div>
    <div class="card">
      <h3>Control en caliente</h3>
      <div class="row"><select id="csess"></select></div>
      <div class="row"><input id="cskills" placeholder="skills: python, fastapi"><input id="cmax" placeholder="max_context_tokens" type="number" min="1024"></div>
      <div class="row"><input id="crules" placeholder="system_contract_rules separadas por coma"></div>
      <div class="row"><button id="inject">Inyectar skills</button><button id="purge">Purge context</button><button id="revoke">Kill switch</button></div>
    </div>
    <div class="card">
      <h3>Plan + dispatch</h3>
      <div class="row"><textarea id="prompt" rows="3" style="width:100%">Implementar mejora y validar contratos</textarea></div>
      <div class="row"><button id="propose">Proponer plan A/B/C</button><button id="dispatch">Dispatch</button></div>
    </div>
    <div class="card">
      <h3>Locks</h3>
      <div id="locks" class="locks muted">sin locks</div>
    </div>
    <div class="card">
      <h3>Consola de eventos</h3>
      <div id="events"></div>
    </div>
  </aside>
</main>
<script>
const $ = (id) => document.getElementById(id);
let KEY = "", ws = null, topo = null;
const STATUS_COLORS = { ONLINE:"#34d399", BUSY:"#fbbf24", OFFLINE:"#8ea2c9", REVOKED:"#f87171" };
const $esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

async function api(path, method="GET", body=null) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type":"application/json", "X-Harness-Admin-Key": KEY },
    body: body === null ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error(`${res.status} ${JSON.stringify(data)}`);
  return data;
}

function logEvent(ev) {
  const box = $("events");
  const line = document.createElement("div");
  line.className = "ev";
  line.innerHTML = `<b>${$esc(ev.event || "event")}</b> <span class="muted">${$esc(ev.created_at || "")}</span>\n${$esc(JSON.stringify(ev.data ?? ev, null, 2))}`;
  box.prepend(line);
  while (box.children.length > 80) box.lastChild.remove();
}

function renderSelects(nodes) {
  for (const id of ["lsrc","ltgt","csess"]) {
    const el = $(id); const prev = el.value;
    el.innerHTML = "";
    nodes.forEach(n => {
      const o = document.createElement("option");
      o.value = n.session_id;
      o.textContent = `${n.engineer_username} (${n.agent_tool})`;
      el.appendChild(o);
    });
    if (prev) el.value = prev;
  }
}

function render(t) {
  topo = t;
  const nodes = t.nodes || [], links = t.links || [];
  renderSelects(nodes);
  const svg = $("graph"); svg.innerHTML = "";
  const W = 900, H = 560, cx = W/2, cy = H/2, R = Math.min(W,H)/2 - 80;
  const pos = {};
  const n = Math.max(1, nodes.length);
  nodes.forEach((node, i) => {
    const a = (2*Math.PI*i/n) - Math.PI/2;
    const x = cx + R*Math.cos(a), y = cy + R*Math.sin(a);
    pos[node.session_id] = {x,y};
    const line = document.createElementNS("http://www.w3.org/2000/svg","line");
    line.setAttribute("x1",x); line.setAttribute("y1",y); line.setAttribute("x2",cx); line.setAttribute("y2",cy);
    line.setAttribute("stroke","#1f2c49"); line.setAttribute("stroke-width","1");
    svg.appendChild(line);
  });
  links.forEach(l => {
    const a = pos[l.source_session_id], b = pos[l.target_session_id];
    if (!a || !b) return;
    const line = document.createElementNS("http://www.w3.org/2000/svg","line");
    line.setAttribute("x1",a.x); line.setAttribute("y1",a.y); line.setAttribute("x2",b.x); line.setAttribute("y2",b.y);
    line.setAttribute("stroke","#60a5fa"); line.setAttribute("stroke-width","2.5"); line.setAttribute("opacity","0.8");
    svg.appendChild(line);
    const label = document.createElementNS("http://www.w3.org/2000/svg","text");
    label.setAttribute("x",(a.x+b.x)/2); label.setAttribute("y",(a.y+b.y)/2-4);
    label.setAttribute("fill","#8ea2c9"); label.setAttribute("font-size","10"); label.setAttribute("text-anchor","middle");
    label.textContent = l.trigger_event;
    svg.appendChild(label);
  });
  nodes.forEach(node => {
    const p = pos[node.session_id]; if (!p) return;
    const color = STATUS_COLORS[node.status] || "#8ea2c9";
    const g = document.createElementNS("http://www.w3.org/2000/svg","g");
    const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
    circle.setAttribute("cx",p.x); circle.setAttribute("cy",p.y); circle.setAttribute("r",26);
    circle.setAttribute("fill","#121a2e"); circle.setAttribute("stroke",color); circle.setAttribute("stroke-width","2.5");
    g.appendChild(circle);
    const tool = document.createElementNS("http://www.w3.org/2000/svg","text");
    tool.setAttribute("x",p.x); tool.setAttribute("y",p.y+4); tool.setAttribute("fill",color);
    tool.setAttribute("font-size","11"); tool.setAttribute("font-weight","700"); tool.setAttribute("text-anchor","middle");
    tool.textContent = String(node.agent_tool || "?").slice(0,9);
    g.appendChild(tool);
    const name = document.createElementNS("http://www.w3.org/2000/svg","text");
    name.setAttribute("x",p.x); name.setAttribute("y",p.y+44); name.setAttribute("fill","#dbe6ff");
    name.setAttribute("font-size","11"); name.setAttribute("text-anchor","middle");
    name.textContent = String(node.engineer_username || node.session_id.slice(0,8));
    g.appendChild(name);
    const pct = Math.max(0, Math.min(100, Number(node.context_pct || 0)));
    const barBg = document.createElementNS("http://www.w3.org/2000/svg","rect");
    barBg.setAttribute("x",p.x-45); barBg.setAttribute("y",p.y+52); barBg.setAttribute("width",90); barBg.setAttribute("height",6);
    barBg.setAttribute("rx",3); barBg.setAttribute("fill","#1b2742");
    g.appendChild(barBg);
    const bar = document.createElementNS("http://www.w3.org/2000/svg","rect");
    bar.setAttribute("x",p.x-45); bar.setAttribute("y",p.y+52); bar.setAttribute("width",90*pct/100); bar.setAttribute("height",6);
    bar.setAttribute("rx",3); bar.setAttribute("fill", pct >= 65 ? "#f87171" : (pct >= 50 ? "#fbbf24" : "#34d399"));
    g.appendChild(bar);
    const label = document.createElementNS("http://www.w3.org/2000/svg","text");
    label.setAttribute("x",p.x); label.setAttribute("y",p.y+72); label.setAttribute("fill","#8ea2c9");
    label.setAttribute("font-size","10"); label.setAttribute("text-anchor","middle");
    label.textContent = `${node.status} ${pct}%`;
    g.appendChild(label);
    svg.appendChild(g);
  });
  const lockBox = $("locks");
  if (!t.locks || !t.locks.length) { lockBox.textContent = "sin locks"; }
  else {
    lockBox.innerHTML = t.locks.map(l => `<div>${$esc(l.repo_name)} :: ${$esc(l.file_path)} <span class="muted">→ ${$esc(l.locked_by_task_id?.slice(0,8))}</span></div>`).join("");
  }
  (t.events || []).slice(0, 12).forEach(e => {
    if (!document.querySelector(`[data-ev-id="${e.id}"]`)) {
      const box = $("events");
      const line = document.createElement("div"); line.className = "ev"; line.dataset.evId = e.id;
      line.innerHTML = `<b>${$esc(e.event_type)}</b> <span class="muted">${$esc(e.created_at || "")}</span>\n${$esc(JSON.stringify(e.payload || {}, null, 2))}`;
      box.appendChild(line);
      while (box.children.length > 80) box.firstChild.remove();
    }
  });
}

async function loadTopology() {
  if (!KEY) return;
  try {
    const t = await api("/api/v1/war-room/topology");
    render(t);
    $("status").textContent = "conectado · " + new Date().toLocaleTimeString();
  } catch (e) {
    $("status").textContent = "error: " + e.message;
  }
}

function connect() {
  KEY = $("key").value.trim();
  if (!KEY) { $("status").textContent = "falta la admin key"; return; }
  if (ws) try { ws.close(); } catch {}
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/war-room`);
  ws.onopen = () => { ws.send(JSON.stringify({type:"AUTH", admin_key: KEY})); };
  ws.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.event === "WAR_ROOM_CONNECTED") { $("status").textContent = "conectado"; loadTopology(); return; }
    if (ev.event === "CONTEXT_UPDATE") { if (topo) loadTopology(); return; }
    logEvent(ev);
    if (["TASK_ASSIGNED","TASK_MERGED","TASK_CONFLICT","SKILLS_INJECTED","PLAN_APPROVED","AGENT_TO_AGENT_RELAY","SESSION_REGISTERED","SESSION_OFFLINE","SESSION_REVOKED"].includes(ev.event)) loadTopology();
  };
  ws.onclose = () => { $("status").textContent = "desconectado"; };
  ws.onerror = () => { $("status").textContent = "error de websocket"; };
}

$("connect").onclick = connect;
$("link").onclick = async () => {
  try {
    await api("/api/v1/war-room/links", "POST", {
      source_session_id: $("lsrc").value, target_session_id: $("ltgt").value,
      link_type: $("ltype").value, trigger_event: $("ltrig").value,
    });
    await loadTopology();
  } catch (e) { logEvent({event:"LINK_ERROR", data:{message:e.message}}); }
};
$("inject").onclick = async () => {
  try {
    const body = { skills: $("cskills").value.split(",").map(s => s.trim()).filter(Boolean) };
    if ($("cmax").value) body.max_context_tokens = Number($("cmax").value);
    if ($("crules").value) body.system_contract_rules = $("crules").value.split(",").map(s => s.trim()).filter(Boolean);
    await api(`/api/v1/sessions/${$("csess").value}/control`, "PATCH", body);
    await loadTopology();
  } catch (e) { logEvent({event:"CONTROL_ERROR", data:{message:e.message}}); }
};
$("purge").onclick = async () => {
  try { await api(`/api/v1/sessions/${$("csess").value}/context-purged`, "POST"); await loadTopology(); }
  catch (e) { logEvent({event:"PURGE_ERROR", data:{message:e.message}}); }
};
$("revoke").onclick = async () => {
  try { await api(`/api/v1/sessions/${$("csess").value}/revoke`, "POST"); await loadTopology(); }
  catch (e) { logEvent({event:"REVOKE_ERROR", data:{message:e.message}}); }
};
$("propose").onclick = async () => {
  try {
    const r = await api("/api/v1/plans/propose", "POST", {
      requested_by: "war-room", repo_name: "harness-self", requirement_prompt: $("prompt").value,
    });
    logEvent({event:"PLAN_PROPOSED", data:{plan_id:r.plan_id, tasks:r.created_tasks}});
    await loadTopology();
  } catch (e) { logEvent({event:"PLAN_ERROR", data:{message:e.message}}); }
};
$("dispatch").onclick = async () => {
  try { const r = await api("/api/v1/tasks/dispatch", "POST"); logEvent({event:"DISPATCH", data:r}); await loadTopology(); }
  catch (e) { logEvent({event:"DISPATCH_ERROR", data:{message:e.message}}); }
};
setInterval(() => { if (KEY && ws && ws.readyState === 1) loadTopology(); }, 5000);
</script>
</body>
</html>
"""
