WAR_ROOM_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harness Control Plane</title>
<style>
  :root {
    --bg:#070b14; --bg-soft:#0b1220; --panel:#0f172a; --panel-2:#111c33;
    --line:#1e2b45; --line-strong:#2b3d5f; --text:#e6eefc; --muted:#8fa3c4;
    --ok:#34d399; --warn:#fbbf24; --bad:#f87171; --accent:#38bdf8; --accent-2:#818cf8;
    --shadow:0 18px 50px rgba(0,0,0,.35); --radius:14px;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body {
    margin:0; background:
      radial-gradient(1000px 600px at 12% -10%, rgba(56,189,248,.10), transparent 55%),
      radial-gradient(900px 500px at 95% 0%, rgba(129,140,248,.10), transparent 55%),
      var(--bg);
    color:var(--text); font:14px/1.45 "Segoe UI",system-ui,-apple-system,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  button, input, select, textarea { font:inherit; color:var(--text); }
  button { cursor:pointer; }
  a { color:var(--accent); }
  .hidden { display:none !important; }
  .muted { color:var(--muted); }
  .mono { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
  .pill { display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:999px; border:1px solid var(--line-strong); font-size:11px; letter-spacing:.02em; white-space:nowrap; }
  .pill.ok { color:var(--ok); border-color:rgba(52,211,153,.45); background:rgba(52,211,153,.08); }
  .pill.warn { color:var(--warn); border-color:rgba(251,191,36,.45); background:rgba(251,191,36,.08); }
  .pill.bad { color:var(--bad); border-color:rgba(248,113,113,.45); background:rgba(248,113,113,.08); }
  .pill.info { color:var(--accent); border-color:rgba(56,189,248,.45); background:rgba(56,189,248,.08); }
  .btn {
    display:inline-flex; align-items:center; justify-content:center; gap:7px;
    border:1px solid var(--line-strong); background:linear-gradient(180deg,#17233c,#111a2e);
    color:var(--text); border-radius:10px; padding:9px 14px; font-weight:600;
    transition:border-color .15s, background .15s, transform .05s; white-space:nowrap;
  }
  .btn:hover { border-color:var(--accent); }
  .btn:active { transform:translateY(1px); }
  .btn.primary { background:linear-gradient(180deg,#0ea5e9,#0284c7); border-color:#0ea5e9; color:#04121f; }
  .btn.primary:hover { filter:brightness(1.08); }
  .btn.danger { background:rgba(248,113,113,.12); border-color:rgba(248,113,113,.5); color:#fecaca; }
  .btn.ghost { background:transparent; }
  .btn.sm { padding:6px 10px; font-size:12px; border-radius:8px; }
  .btn:disabled { opacity:.45; cursor:not-allowed; }
  .field { display:flex; flex-direction:column; gap:6px; min-width:0; }
  .field label { font-size:12px; color:var(--muted); font-weight:600; letter-spacing:.02em; }
  input, select, textarea {
    width:100%; background:#0a1322; border:1px solid var(--line); border-radius:10px;
    padding:10px 12px; outline:none; transition:border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus, textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(56,189,248,.12); }
  textarea { resize:vertical; min-height:84px; }
  .card {
    background:linear-gradient(180deg,rgba(17,28,51,.96),rgba(15,23,42,.96));
    border:1px solid var(--line); border-radius:var(--radius); padding:16px; min-width:0;
  }
  .card h3 { margin:0 0 10px; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.09em; }
  .card h2 { margin:0 0 4px; font-size:17px; }
  .stack { display:flex; flex-direction:column; gap:14px; min-width:0; }
  .row { display:flex; gap:9px; align-items:center; flex-wrap:wrap; }
  .row > .grow { flex:1 1 180px; min-width:0; }
  .grid-2 { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  .divider { height:1px; background:var(--line); margin:6px 0; }

  /* ---------- Login ---------- */
  .login-wrap { min-height:100dvh; display:grid; place-items:center; padding:22px; }
  .login-card { width:min(920px,100%); display:grid; grid-template-columns:1.05fr .95fr; border:1px solid var(--line); border-radius:22px; overflow:hidden; box-shadow:var(--shadow); background:var(--panel); }
  .login-brand { padding:38px; background:
    radial-gradient(500px 300px at 0% 0%, rgba(56,189,248,.20), transparent 60%),
    linear-gradient(145deg,#0b1527,#0a101d); display:flex; flex-direction:column; justify-content:space-between; gap:28px; }
  .brand-mark { display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); }
  .brand-mark .dot { width:12px; height:12px; border-radius:4px; background:linear-gradient(135deg,var(--accent),var(--accent-2)); box-shadow:0 0 24px rgba(56,189,248,.7); }
  .login-brand h1 { margin:0; font-size:clamp(26px,3vw,40px); line-height:1.08; letter-spacing:-.02em; }
  .login-brand p { margin:0; color:var(--muted); max-width:38ch; }
  .login-points { display:flex; flex-direction:column; gap:9px; color:#c7d7f2; font-size:13px; }
  .login-points span::before { content:"+"; color:var(--ok); margin-right:8px; font-weight:800; }
  .login-form { padding:34px; display:flex; flex-direction:column; gap:14px; }
  .login-tabs { display:flex; gap:6px; background:#0a1322; border:1px solid var(--line); border-radius:11px; padding:4px; }
  .login-tabs button { flex:1; border:0; background:transparent; color:var(--muted); border-radius:8px; padding:9px; font-weight:700; }
  .login-tabs button.active { background:#17233c; color:var(--text); }
  .login-error { color:#fecaca; background:rgba(248,113,113,.10); border:1px solid rgba(248,113,113,.35); border-radius:10px; padding:9px 11px; font-size:12px; }
  .login-note { font-size:12px; color:var(--muted); }

  /* ---------- App shell ---------- */
  .app { min-height:100dvh; }
  .shell { min-height:100dvh; }
  .sidebar { border-right:1px solid var(--line); background:rgba(9,15,27,.92); padding:18px 14px; display:flex; flex-direction:column; gap:16px; }
  .sidebar .brand { display:flex; align-items:center; gap:9px; font-weight:800; letter-spacing:.10em; font-size:12px; color:var(--accent); text-transform:uppercase; padding:4px 8px 10px; }
  .side-label { font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:#5f7397; padding:0 10px; }
  .nav-list { display:flex; flex-direction:column; gap:5px; }
  .nav-btn { display:flex; align-items:center; gap:10px; width:100%; text-align:left; padding:10px 11px; border-radius:9px; border:1px solid transparent; background:transparent; color:#bacae5; font-weight:650; }
  .nav-btn:hover { background:#101a2d; }
  .nav-btn.active { background:linear-gradient(90deg,rgba(56,189,248,.16),rgba(56,189,248,.04)); border-color:rgba(56,189,248,.30); color:#eaf6ff; }
  .nav-btn .ico { width:20px; text-align:center; color:var(--accent); }
  .side-projects { display:flex; flex-direction:column; gap:4px; overflow:auto; min-height:0; }
  .side-project { display:flex; align-items:center; gap:8px; width:100%; text-align:left; padding:8px 10px; border-radius:8px; border:1px solid transparent; background:transparent; color:#a9bbd9; font-size:12.5px; }
  .side-project:hover { background:#101a2d; }
  .side-project .sq { width:7px; height:7px; border-radius:2px; background:var(--accent); flex:0 0 auto; }
  .side-foot { margin-top:auto; padding:10px; border-top:1px solid var(--line); font-size:11px; color:#657a9e; }
  .main { min-width:0; display:flex; flex-direction:column; }
  .topbar { display:flex; align-items:center; gap:10px; padding:12px 18px; border-bottom:1px solid var(--line); background:rgba(10,17,31,.82); position:sticky; top:0; z-index:30; backdrop-filter:blur(10px); }
  .topbar .title { font-weight:800; letter-spacing:.02em; }
  .topbar .spacer { flex:1; }
  .user-chip { display:flex; align-items:center; gap:8px; padding:5px 10px 5px 6px; border:1px solid var(--line); border-radius:999px; background:#0b1424; font-size:12px; }
  .avatar { width:26px; height:26px; border-radius:50%; display:grid; place-items:center; background:linear-gradient(135deg,var(--accent),var(--accent-2)); color:#04121f; font-weight:800; font-size:11px; }
  .menu-btn { display:none; }
  .content { padding:18px; display:flex; flex-direction:column; gap:16px; }
  .view { display:flex; flex-direction:column; gap:16px; min-width:0; }
  .view-head { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; }
  .view-head h1 { margin:0; font-size:22px; letter-spacing:-.02em; }
  .view-head p { margin:2px 0 0; color:var(--muted); font-size:13px; }
  .view-head .spacer { flex:1; }
  .project-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
  .project-card { display:flex; flex-direction:column; gap:10px; }
  .project-card .meta { display:flex; flex-wrap:wrap; gap:6px; }
  .project-card .skills { display:flex; flex-wrap:wrap; gap:5px; }
  .stat-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
  .stat { background:#0a1322; border:1px solid var(--line); border-radius:9px; padding:8px; text-align:center; }
  .stat b { display:block; font-size:17px; }
  .stat span { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
  .table-wrap { width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; border:1px solid var(--line); border-radius:11px; }
  table { border-collapse:collapse; width:100%; min-width:720px; }
  th,td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { font-size:10px; letter-spacing:.09em; text-transform:uppercase; color:#7288ad; background:#0a1322; }
  tr:last-child td { border-bottom:0; }
  .form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  .form-grid .span-2 { grid-column:span 2; }
  .check-row { display:flex; align-items:flex-start; gap:10px; padding:9px 10px; border:1px solid var(--line); border-radius:9px; background:#0a1322; }
  .check-row input[type=checkbox] { width:18px; height:18px; margin-top:1px; accent-color:var(--accent); }
  .check-row .grow { flex:1; min-width:0; }
  .check-row .skills-input { margin-top:7px; }
  .result-box { background:#08111f; border:1px dashed var(--line-strong); border-radius:10px; padding:10px; font-size:12px; white-space:pre-wrap; word-break:break-word; }
  .copy-row { display:flex; gap:8px; align-items:flex-start; }
  .copy-row textarea { min-height:78px; font:12px/1.45 ui-monospace,Consolas,monospace; }
  .copy-row .btn { flex:0 0 auto; }

  /* ---------- War room ---------- */
  .war-head { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .war-head .spacer { flex:1; }
  .war-grid { display:grid; grid-template-columns:minmax(0,1fr); gap:14px; min-width:0; }
  .war-left, .war-center, .war-right { display:flex; flex-direction:column; gap:14px; min-width:0; }
  .session-list { display:flex; flex-direction:column; gap:7px; max-height:260px; overflow:auto; }
  .session-item { display:flex; align-items:center; gap:9px; width:100%; text-align:left; padding:9px 10px; border:1px solid var(--line); background:#0a1322; border-radius:9px; }
  .session-item:hover { border-color:var(--line-strong); }
  .session-item.active { border-color:var(--accent); background:rgba(56,189,248,.08); }
  .session-item .who { min-width:0; flex:1; }
  .session-item .who b { display:block; font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .session-item .who span { display:block; font-size:11px; color:var(--muted); }
  .graph-card { padding:10px; }
  .graph-wrap { width:100%; overflow:hidden; border-radius:10px; background:
    radial-gradient(500px 300px at 50% 40%, rgba(56,189,248,.07), transparent 65%), #08111f; }
  #graph { display:block; width:100%; height:auto; aspect-ratio:900/560; }
  .task-list { display:grid; grid-template-columns:1fr; gap:9px; max-height:360px; overflow:auto; }
  .task-card { border:1px solid var(--line); background:#0a1322; border-radius:10px; padding:10px; display:flex; flex-direction:column; gap:6px; }
  .task-card .top { display:flex; gap:8px; align-items:flex-start; }
  .task-card .top b { flex:1; min-width:0; font-size:13px; }
  .task-card .meta { display:flex; flex-wrap:wrap; gap:6px; font-size:11px; color:var(--muted); }
  .console { height:230px; overflow:auto; background:#060c17; border:1px solid var(--line); border-radius:10px; padding:9px; font:11.5px/1.45 ui-monospace,Consolas,monospace; white-space:pre-wrap; word-break:break-word; }
  .console .ev { border-bottom:1px dashed #16213a; padding:4px 0; }
  .console .ev b { color:var(--accent); }
  .locks { max-height:110px; overflow:auto; font:11.5px/1.5 ui-monospace,Consolas,monospace; }
  .inspector-empty { color:var(--muted); font-size:13px; padding:6px 0; }

  /* ---------- Bottom nav / drawer ---------- */
  .bottom-nav { display:none; }
  .drawer-backdrop { display:none; position:fixed; inset:0; background:rgba(2,6,14,.66); z-index:40; }
  .drawer-backdrop.open { display:block; }

  /* ---------- Toast / modal ---------- */
  #toast { position:fixed; right:18px; bottom:18px; z-index:80; display:flex; flex-direction:column; gap:8px; max-width:min(420px,calc(100vw - 36px)); }
  .toast-item { background:#101c33; border:1px solid var(--line-strong); border-left:3px solid var(--accent); border-radius:10px; padding:10px 13px; box-shadow:var(--shadow); font-size:13px; animation:slide .18s ease-out; }
  .toast-item.bad { border-left-color:var(--bad); }
  .toast-item.ok { border-left-color:var(--ok); }
  @keyframes slide { from { transform:translateY(8px); opacity:0; } to { transform:none; opacity:1; } }
  .modal { position:fixed; inset:0; z-index:70; display:grid; place-items:center; padding:18px; background:rgba(2,6,14,.72); }
  .modal-card { width:min(640px,100%); max-height:90dvh; overflow:auto; background:var(--panel); border:1px solid var(--line-strong); border-radius:16px; box-shadow:var(--shadow); }
  .modal-head { display:flex; align-items:center; gap:10px; padding:15px 17px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); z-index:1; }
  .modal-head h3 { margin:0; flex:1; font-size:15px; }
  .modal-body { padding:17px; }
  .modal-foot { padding:13px 17px; border-top:1px solid var(--line); display:flex; justify-content:flex-end; gap:8px; }

  /* 16-inch / wide desktop: 3 columns */
  @media (min-width: 1200px) {
    .shell { display:grid; grid-template-columns:260px minmax(0,1fr); }
    .sidebar { position:sticky; top:0; height:100dvh; }
    .content { padding:20px 22px 26px; }
    .war-grid { grid-template-columns:minmax(0,1fr) 360px; align-items:start; }
    .war-right { position:sticky; top:74px; }
  }

  /* Tablet + large mobile: drawer and stacked cards */
  @media (max-width: 1024px) {
    .sidebar { position:fixed; inset:0 auto 0 0; width:min(310px,86vw); z-index:50; transform:translateX(-102%); transition:transform .2s ease; box-shadow:var(--shadow); }
    .sidebar.open { transform:none; }
    .menu-btn { display:inline-flex; }
    .bottom-nav { display:flex; position:fixed; left:0; right:0; bottom:0; z-index:45; background:rgba(9,15,27,.97); border-top:1px solid var(--line); padding:5px 6px calc(5px + env(safe-area-inset-bottom)); gap:4px; }
    .bottom-nav button { flex:1; min-height:48px; border:0; background:transparent; color:#93a7c8; border-radius:9px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; font-size:10px; font-weight:700; }
    .bottom-nav button .ico { font-size:16px; color:inherit; }
    .bottom-nav button.active { color:var(--accent); background:rgba(56,189,248,.08); }
    .content { padding:14px 14px 92px; }
    .war-grid { grid-template-columns:1fr; }
    .project-grid { grid-template-columns:1fr; }
    .form-grid { grid-template-columns:1fr; }
    .form-grid .span-2 { grid-column:auto; }
    .btn, .nav-btn, .side-project, .session-item, .login-tabs button { min-height:44px; }
    .grid-2 { grid-template-columns:1fr; }
    .war-right { position:static; }
  }

  /* 7-inch / compact phones */
  @media (max-width: 600px) {
    .login-card { grid-template-columns:1fr; }
    .login-brand { padding:24px; gap:18px; }
    .login-brand .login-points { display:none; }
    .login-form { padding:22px; }
    .topbar { padding:10px 12px; }
    .topbar .title { font-size:13px; }
    .user-chip .who-text { display:none; }
    .content { padding:12px 10px 94px; gap:12px; }
    .view-head h1 { font-size:19px; }
    .project-grid { grid-template-columns:1fr; }
    .stat-grid { grid-template-columns:1fr 1fr 1fr; }
    .war-grid { gap:10px; }
    #graph { aspect-ratio:1/1; }
    .task-list { max-height:none; }
    .console { height:190px; }
    .modal { padding:8px; }
    .modal-card { max-height:94dvh; border-radius:13px; }
    .toast-item { font-size:12px; }
    .form-grid .span-2 { grid-column:auto; }
    .copy-row { flex-direction:column; }
    .copy-row .btn { width:100%; }
  }
</style>
</head>
<body>

<div id="loginView" class="login-wrap">
  <div class="login-card">
    <section class="login-brand">
      <div class="brand-mark"><span class="dot"></span> Harness Control Plane</div>
      <div>
        <h1>Un proyecto.<br>Una sesión.<br>Cero fugas.</h1>
        <p>Gobierno de agentes, context packs quirúrgicos y War Room en tiempo real. Ingenieros y administradores en un solo lugar.</p>
      </div>
      <div class="login-points">
        <span>Aislamiento estricto por proyecto</span>
        <span>Configuración MCP lista para copiar</span>
        <span>Dual Plan, dispatch y kill switch</span>
      </div>
    </section>
    <section class="login-form">
      <div>
        <h2 style="margin:0 0 4px">Acceso al Portal</h2>
        <div class="muted" style="font-size:13px">Inicia sesión con tu correo corporativo.</div>
      </div>
      <div class="login-tabs">
        <button id="loginTabEmail" class="active" type="button">Correo y contraseña</button>
        <button id="loginTabKey" type="button">Admin API Key</button>
      </div>
      <div id="loginEmailPane" class="stack" style="gap:12px">
        <form id="loginForm" class="stack" style="gap:12px">
          <div class="field"><label for="loginEmail">Email</label><input id="loginEmail" type="email" autocomplete="username" placeholder="dev1@empresa.com" required></div>
          <div class="field"><label for="loginPassword">Contraseña</label><input id="loginPassword" type="password" autocomplete="current-password" placeholder="••••••••" required></div>
          <button id="loginBtn" class="btn primary" type="submit" style="width:100%;padding:11px">Entrar al Portal</button>
        </form>
      </div>
      <div id="loginKeyPane" class="stack hidden" style="gap:12px">
        <div class="field"><label for="adminKey">X-Harness-Admin-Key</label><input id="adminKey" type="password" autocomplete="off" placeholder="Clave de automatización"></div>
        <button id="adminKeyBtn" class="btn" type="button" style="width:100%;padding:11px">Entrar con Admin Key</button>
        <div class="login-note">Compatible con scripts y automatización. La clave no se guarda al recargar.</div>
      </div>
      <div id="loginError" class="login-error hidden"></div>
      <div class="login-note">La sesión se guarda solo en esta pestaña. Cierra sesión al terminar.</div>
    </section>
  </div>
</div>

<div id="appView" class="app hidden">
  <div class="shell">
    <aside id="sidebar" class="sidebar">
      <div class="brand"><span class="dot" style="width:11px;height:11px;border-radius:3px;background:linear-gradient(135deg,var(--accent),var(--accent-2))"></span> Harness</div>
      <div>
        <div class="side-label">Navegación</div>
        <nav class="nav-list" id="desktopNav" aria-label="Navegación principal"></nav>
      </div>
      <div style="min-height:0;display:flex;flex-direction:column;gap:6px;flex:1">
        <div class="side-label">Proyectos</div>
        <div id="sidebarProjects" class="side-projects"></div>
      </div>
      <div class="side-foot">Control Plane v7 · Aislamiento 1:1</div>
    </aside>

    <div class="main">
      <header class="topbar">
        <button id="menuBtn" class="btn ghost menu-btn" type="button" aria-label="Abrir menú">☰</button>
        <div class="title" id="topTitle">Harness</div>
        <div class="spacer"></div>
        <div class="user-chip"><span class="avatar" id="userInitials">?</span><span class="who-text" id="userLabel">—</span></div>
        <button id="logoutBtn" class="btn sm ghost" type="button">Salir</button>
      </header>

      <div class="content">
        <section id="viewProjects" class="view">
          <div class="view-head">
            <div><h1>Proyectos</h1><p>Repositorios gobernados, miembros aprobados y sesiones activas.</p></div>
            <div class="spacer"></div>
            <button id="newProjectBtn" class="btn primary" type="button">+ Crear Nuevo Proyecto</button>
          </div>
          <div id="projectsGrid" class="project-grid"></div>
        </section>

        <section id="viewEngineers" class="view hidden">
          <div class="view-head">
            <div><h1>Ingenieros y Accesos</h1><p>Invita por correo, aprueba proyectos y define skills por membresía.</p></div>
          </div>
          <div class="grid-2" style="align-items:start">
            <div class="card stack">
              <h3>Crear / invitar ingeniero</h3>
              <div class="form-grid">
                <div class="field"><label for="eEmail">Email</label><input id="eEmail" type="email" placeholder="dev1@empresa.com"></div>
                <div class="field"><label for="eName">Nombre completo</label><input id="eName" placeholder="Dev Uno"></div>
                <div class="field"><label for="ePassword">Contraseña inicial (opcional)</label><input id="ePassword" type="password" autocomplete="new-password" placeholder="Si se omite, se genera"></div>
                <div class="field"><label for="eInvite">Invite token (opcional)</label><input id="eInvite" placeholder="token de invitación"></div>
              </div>
              <div class="field">
                <label>Proyectos aprobados y skills</label>
                <div id="eProjects" class="stack" style="gap:7px"></div>
              </div>
              <button id="createEngineerBtn" class="btn primary" type="button">Crear / Invitar Ingeniero</button>
              <div id="engineerResult" class="result-box hidden"></div>
            </div>
            <div class="card stack">
              <h3>Accesos por proyecto</h3>
              <div class="row"><input id="engSearch" class="grow" placeholder="Filtrar por correo o proyecto"></div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Ingeniero</th><th>Proyecto</th><th>Skills</th><th>Estado</th><th></th></tr></thead>
                  <tbody id="engineersTable"></tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        <section id="viewWarRoom" class="view hidden">
          <div class="view-head">
            <div><h1>Sala de Control</h1><p>Topología, tareas y control en caliente por proyecto.</p></div>
            <div class="spacer"></div>
            <span id="warStatus" class="pill">WS: desconectado</span>
          </div>
          <div class="war-head">
            <div class="field" style="min-width:230px">
              <label for="warFilter">Filtro de proyecto</label>
              <select id="warFilter"></select>
            </div>
            <div class="spacer"></div>
            <button id="warRefresh" class="btn sm" type="button">Refrescar</button>
          </div>
          <div class="war-grid">
            <div class="war-left">
              <div class="card">
                <h3>Sesiones conectadas</h3>
                <div id="warSessions" class="session-list"></div>
              </div>
              <div class="card">
                <h3>Dual Plan + Dispatch</h3>
                <div class="stack" style="gap:9px">
                  <div class="field"><label for="warPlanProject">Proyecto</label><select id="warPlanProject"></select></div>
                  <div class="field"><label for="warPrompt">Requerimiento</label><textarea id="warPrompt" rows="3" placeholder="Implementar mejora y validar contratos"></textarea></div>
                  <div class="row"><button id="proposeBtn" class="btn sm" type="button">Proponer Plan A/B/C</button><button id="dispatchBtn" class="btn sm primary" type="button">Dispatch</button></div>
                  <div id="planResult" class="result-box hidden"></div>
                </div>
              </div>
              <div class="card">
                <h3>Locks</h3>
                <div id="warLocks" class="locks muted">sin locks</div>
              </div>
              <div class="card">
                <h3>Repos indexados</h3>
                <div id="repo" class="locks muted">cargando…</div>
              </div>
            </div>

            <div class="war-center">
              <div class="card graph-card">
                <h3>Topología en vivo</h3>
                <div class="graph-wrap"><svg id="graph" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Grafo de sesiones y enlaces"></svg></div>
                <div class="row" style="margin-top:8px">
                  <span class="pill info" id="graphCount">0 sesiones</span>
                  <span class="muted" style="font-size:11px">Toca un nodo para inspeccionar y controlar.</span>
                </div>
              </div>
              <div class="card">
                <h3>Tareas en curso</h3>
                <div id="warTasks" class="task-list"></div>
              </div>
            </div>

            <div class="war-right">
              <div class="card">
                <h3>Inspector</h3>
                <div id="warInspector"><div class="inspector-empty">Selecciona una sesión en el grafo o en la lista.</div></div>
              </div>
              <div class="card stack">
                <h3>Control en caliente</h3>
                <div class="field"><label for="cSkills">Skills (separadas por coma)</label><input id="cSkills" placeholder="python, fastapi"></div>
                <div class="field"><label for="cRules">Reglas de contrato</label><input id="cRules" placeholder="no-tocar-migraciones, tests-obligatorios"></div>
                <div class="field"><label for="cMax">max_context_tokens</label><input id="cMax" type="number" min="1024" placeholder="32000"></div>
                <div class="row"><button id="injectBtn" class="btn sm" type="button">Inyectar</button><button id="purgeBtn" class="btn sm" type="button">Purge context</button><button id="killBtn" class="btn sm danger" type="button">Kill Switch</button></div>
              </div>
              <div class="card stack">
                <h3>Enlaces entre agentes</h3>
                <div class="grid-2">
                  <div class="field"><label for="linkSrc">Origen</label><select id="linkSrc"></select></div>
                  <div class="field"><label for="linkTgt">Destino</label><select id="linkTgt"></select></div>
                  <div class="field"><label for="linkType">Tipo</label><select id="linkType"><option>PIPELINE_HANDOFF</option><option>PEER_REVIEW</option><option>CONTEXT_RELAY</option><option>SUPERVISOR</option></select></div>
                  <div class="field"><label for="linkTrigger">Trigger</label><select id="linkTrigger"><option>MANUAL_RELAY</option><option>ON_TASK_MERGED</option><option>ON_CONFLICT</option></select></div>
                </div>
                <button id="createLinkBtn" class="btn sm" type="button">Crear enlace</button>
              </div>
              <div class="card">
                <h3>Consola en vivo</h3>
                <div id="warConsole" class="console"></div>
              </div>
            </div>
          </div>
        </section>

        <section id="viewMyProjects" class="view hidden">
          <div class="view-head">
            <div><h1>Mis Proyectos Aprobados</h1><p>Una sesión independiente por proyecto. Tu configuración MCP lista para copiar.</p></div>
            <div class="spacer"></div>
            <button id="myRefresh" class="btn sm" type="button">Refrescar</button>
          </div>
          <div id="myProjectsGrid" class="project-grid"></div>
        </section>
      </div>
    </div>
  </div>

  <nav class="bottom-nav" id="bottomNav" aria-label="Navegación móvil"></nav>
</div>

<div id="drawerBackdrop" class="drawer-backdrop"></div>
<div id="toast"></div>

<div id="modal" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
  <div class="modal-card">
    <div class="modal-head"><h3 id="modalTitle">Crear Nuevo Proyecto</h3><button id="modalClose" class="btn sm ghost" type="button" aria-label="Cerrar">✕</button></div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-foot" id="modalActions"></div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const STATUS_COLORS = { ONLINE:"#34d399", BUSY:"#fbbf24", OFFLINE:"#8fa3c4", REVOKED:"#f87171" };
const TOOL_LABELS = { opencode:"OpenCode", claudecode:"Claude Code", cursor:"Cursor", hermes:"Hermes", pi:"Pi", custom:"Custom" };
const state = {
  token: null, adminKey: null, user: null, role: null,
  projects: [], topology: null, activeView: "projects",
  projectFilter: "all", selectedSession: null, ws: null,
  sessionsByProject: {}, bundles: {}, consoleLines: 0,
};
let refreshTimer = null;
let wsRetry = null;
let topoRefreshTimer = null;

function toast(message, kind = "") {
  const box = $("toast");
  const item = document.createElement("div");
  item.className = "toast-item " + kind;
  item.textContent = message;
  box.appendChild(item);
  setTimeout(() => item.remove(), 5200);
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.body !== undefined && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  else if (state.adminKey) headers["X-Harness-Admin-Key"] = state.adminKey;
  const res = await fetch(path, Object.assign({}, options, { headers }));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data && typeof data === "object" ? (data.detail || JSON.stringify(data)) : String(data || res.statusText);
    throw new Error(res.status + " · " + detail);
  }
  return data;
}

function showLogin(message = "") {
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
  $("loginError").textContent = message;
  $("loginError").classList.toggle("hidden", !message);
  if (state.ws) { try { state.ws.close(); } catch {} state.ws = null; }
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
}

function setLoginTab(which) {
  const email = which === "email";
  $("loginTabEmail").classList.toggle("active", email);
  $("loginTabKey").classList.toggle("active", !email);
  $("loginEmailPane").classList.toggle("hidden", !email);
  $("loginKeyPane").classList.toggle("hidden", email);
  $("loginError").classList.add("hidden");
}

function capital(v) { return String(v || "?").slice(0, 2).toUpperCase(); }

function navItems() {
  if (state.role === "ADMIN") {
    return [
      { id:"projects", icon:"▦", label:"Proyectos", bottom:"Proyectos" },
      { id:"engineers", icon:"☺", label:"Ingenieros y Accesos", bottom:"Equipo" },
      { id:"warroom", icon:"◎", label:"Sala de Control", bottom:"War Room" },
    ];
  }
  return [{ id:"myprojects", icon:"▤", label:"Mis Proyectos", bottom:"Proyectos" }];
}

function buildNav() {
  const items = navItems();
  $("desktopNav").innerHTML = items.map(i => `<button class="nav-btn" data-view="${i.id}" type="button"><span class="ico">${i.icon}</span><span>${i.label}</span></button>`).join("");
  $("bottomNav").innerHTML = items.map(i => `<button data-view="${i.id}" type="button"><span class="ico">${i.icon}</span><span>${i.bottom}</span></button>`).join("");
  document.querySelectorAll("[data-view]").forEach(btn => {
    btn.onclick = () => setView(btn.dataset.view);
  });
}

function setView(name) {
  state.activeView = name;
  const views = { projects:"viewProjects", engineers:"viewEngineers", warroom:"viewWarRoom", myprojects:"viewMyProjects" };
  Object.values(views).forEach(id => $(id).classList.add("hidden"));
  if (views[name]) $(views[name]).classList.remove("hidden");
  document.querySelectorAll("[data-view]").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
  $("topTitle").textContent = ({projects:"Proyectos", engineers:"Ingenieros y Accesos", warroom:"Sala de Control", myprojects:"Mis Proyectos"})[name] || "Harness";
  closeDrawer();
  if (name === "warroom") { if (state.role === "ADMIN") { ensureWarRoom(); } }
  if (name === "engineers") renderEngineers();
  if (name === "projects") renderProjects();
  if (name === "myprojects") renderMyProjects();
}

function openDrawer() { $("sidebar").classList.add("open"); $("drawerBackdrop").classList.add("open"); }
function closeDrawer() { $("sidebar").classList.remove("open"); $("drawerBackdrop").classList.remove("open"); }

async function doEmailLogin(event) {
  event.preventDefault();
  const email = $("loginEmail").value.trim();
  const password = $("loginPassword").value;
  if (!email || !password) { showLogin("Completa email y contraseña."); return; }
  $("loginBtn").disabled = true;
  try {
    const data = await api("/api/v1/auth/login", { method:"POST", body:JSON.stringify({ email, password }) });
    state.token = data.access_token;
    state.adminKey = null;
    state.user = data.user;
    state.role = data.user.role;
    sessionStorage.setItem("harness.token", state.token);
    $("loginPassword").value = "";
    await enterApp();
  } catch (err) {
    showLogin(err.message);
  } finally {
    $("loginBtn").disabled = false;
  }
}

async function doKeyLogin() {
  const key = $("adminKey").value.trim();
  if (!key) { showLogin("Ingresa la Admin API Key."); return; }
  state.adminKey = key;
  state.token = null;
  state.user = { email:"admin-key", full_name:"Admin API Key", role:"ADMIN" };
  state.role = "ADMIN";
  $("adminKey").value = "";
  try {
    await enterApp();
  } catch (err) {
    state.adminKey = null;
    showLogin(err.message);
  }
}

async function bootstrapToken() {
  const saved = sessionStorage.getItem("harness.token");
  if (!saved) return;
  state.token = saved;
  try {
    const me = await api("/api/v1/auth/me");
    state.user = me.user;
    state.role = me.user.role;
    await enterApp();
  } catch (err) {
    sessionStorage.removeItem("harness.token");
    state.token = null;
    showLogin("Tu sesión expiró. Inicia sesión de nuevo.");
  }
}

async function enterApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  $("userLabel").textContent = state.user.full_name || state.user.email || "Usuario";
  $("userInitials").textContent = capital(state.user.full_name || state.user.email);
  $("logoutBtn").classList.remove("hidden");
  buildNav();
  await refreshProjects();
  if (state.role === "ADMIN") {
    $("viewProjects").classList.remove("hidden");
    setView("projects");
  } else {
    setView("myprojects");
  }
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = state.role === "ADMIN"
    ? setInterval(() => { if (state.activeView !== "warroom") refreshProjects().catch(() => {}); }, 20000)
    : null;
}

function logout() {
  sessionStorage.removeItem("harness.token");
  if (state.ws) { try { state.ws.close(); } catch {} }
  state.token = null; state.adminKey = null; state.user = null; state.role = null;
  state.projects = []; state.topology = null; state.selectedSession = null;
  state.sessionsByProject = {}; state.bundles = {};
  $("userLabel").textContent = "—"; $("userInitials").textContent = "?";
  showLogin("");
}

async function refreshProjects() {
  if (!state.user) return;
  const data = await api("/api/v1/projects");
  state.projects = data.projects || [];
  renderSidebarProjects();
  if (state.role === "ADMIN") {
    renderProjects();
    renderProjectChoosers();
  } else {
    renderMyProjects();
  }
}

function projectById(id) { return state.projects.find(p => p.id === id); }

function renderSidebarProjects() {
  const box = $("sidebarProjects");
  if (!state.projects.length) { box.innerHTML = '<div class="muted" style="font-size:12px;padding:6px 10px">Sin proyectos</div>'; return; }
  box.innerHTML = state.projects.map(p => `<button class="side-project" data-pid="${esc(p.id)}" type="button"><span class="sq"></span><span>${esc(p.project_slug)}</span></button>`).join("");
  box.querySelectorAll(".side-project").forEach(btn => {
    btn.onclick = () => {
      state.projectFilter = btn.dataset.pid;
      if (state.role === "ADMIN") { $("warFilter").value = state.projectFilter; setView("warroom"); renderWarRoom(); }
      else { setView("myprojects"); }
    };
  });
}

function sessionPill(status) {
  const cls = status === "ONLINE" || status === "BUSY" ? (status === "ONLINE" ? "ok" : "warn") : (status === "REVOKED" ? "bad" : "");
  return `<span class="pill ${cls}">${esc(status)}</span>`;
}

function renderProjects() {
  const grid = $("projectsGrid");
  if (!grid) return;
  if (!state.projects.length) {
    grid.innerHTML = '<div class="card"><div class="muted">Aún no hay proyectos. Crea el primero para inicializar git, el README base y el índice AST.</div></div>';
    return;
  }
  grid.innerHTML = state.projects.map(p => {
    const members = p.members || [];
    const sessions = p.active_sessions || [];
    return `<article class="card project-card">
      <div class="row"><div style="flex:1;min-width:0"><h2>${esc(p.display_name)}</h2><div class="muted mono" style="font-size:12px">${esc(p.project_slug)}</div></div><span class="pill ${sessions.length ? "ok" : ""}">${sessions.length} sesiones activas</span></div>
      <div class="muted" style="font-size:12.5px">${esc(p.description || "Sin descripción")}</div>
      <div class="meta"><span class="pill">${esc(p.default_branch || "main")}</span>${p.repo_url ? `<span class="pill info">repo remoto</span>` : `<span class="pill">repo local</span>`}<span class="pill">${members.length} miembros</span></div>
      <div class="divider"></div>
      <div><div class="side-label" style="padding:0 0 6px">Miembros aprobados</div>${members.length ? members.map(m => `<div class="row" style="justify-content:space-between;font-size:12px;padding:2px 0"><span>${esc(m.email)}</span><span class="muted">${esc((m.allowed_skills || []).join(", ") || "sin skills")}</span></div>`).join("") : '<div class="muted" style="font-size:12px">Sin miembros</div>'}</div>
      <div class="row" style="margin-top:auto"><button class="btn sm" data-war-pid="${esc(p.id)}" type="button">Abrir War Room</button></div>
    </article>`;
  }).join("");
  grid.querySelectorAll("[data-war-pid]").forEach(btn => {
    btn.onclick = () => { state.projectFilter = btn.dataset.warPid; setView("warroom"); };
  });
}

function renderProjectChoosers() {
  const filter = $("warFilter");
  const plan = $("warPlanProject");
  if (!filter || !plan) return;
  const current = state.projectFilter;
  filter.innerHTML = `<option value="all">Vista global (todos)</option>` + state.projects.map(p => `<option value="${esc(p.id)}">${esc(p.display_name)} · ${esc(p.project_slug)}</option>`).join("");
  filter.value = state.projects.some(p => p.id === current) ? current : "all";
  state.projectFilter = filter.value;
  const prevPlan = plan.value;
  plan.innerHTML = state.projects.map(p => `<option value="${esc(p.id)}">${esc(p.display_name)} · ${esc(p.project_slug)}</option>`).join("");
  if (prevPlan && [...plan.options].some(o => o.value === prevPlan)) plan.value = prevPlan;
}

function renderEngineers() {
  const tbody = $("engineersTable");
  const container = $("eProjects");
  if (!tbody || !container) return;
  container.innerHTML = state.projects.map(p => `<div class="check-row" data-eng-project="${esc(p.id)}"><input type="checkbox" class="eng-project-check"><div class="grow"><div style="font-weight:650">${esc(p.display_name)} <span class="muted mono" style="font-size:11px">${esc(p.project_slug)}</span></div><input class="skills-input" placeholder="skills: python, fastapi"></div></div>`).join("");
  container.querySelectorAll(".eng-project-check").forEach(chk => {
    chk.onchange = () => { const input = chk.closest(".check-row").querySelector(".skills-input"); input.disabled = !chk.checked; };
    chk.dispatchEvent(new Event("change"));
  });
  const q = ($("engSearch").value || "").toLowerCase();
  const rows = [];
  state.projects.forEach(p => (p.members || []).forEach(m => rows.push({ project:p, member:m })));
  const filtered = rows.filter(r => !q || (r.member.email || "").toLowerCase().includes(q) || r.project.project_slug.toLowerCase().includes(q));
  tbody.innerHTML = filtered.length ? filtered.map(r => `<tr>
    <td><b>${esc(r.member.email)}</b><div class="muted" style="font-size:11px">${esc(r.member.full_name || "")}</div></td>
    <td><span class="mono" style="font-size:12px">${esc(r.project.project_slug)}</span></td>
    <td>${(r.member.allowed_skills || []).map(s => `<span class="pill">${esc(s)}</span>`).join(" ") || '<span class="muted">—</span>'}</td>
    <td>${sessionPill(r.member.status || "APPROVED")}</td>
    <td><button class="btn sm danger" data-revoke-project="${esc(r.project.id)}" data-revoke-user="${esc(r.member.user_id)}" type="button">Revocar</button></td>
  </tr>`).join("") : `<tr><td colspan="5" class="muted">Sin accesos registrados.</td></tr>`;
  tbody.querySelectorAll("[data-revoke-project]").forEach(btn => {
    btn.onclick = async () => {
      const projectId = btn.dataset.revokeProject, userId = btn.dataset.revokeUser;
      if (!confirm("¿Revocar el acceso y cerrar sus sesiones en este proyecto?")) return;
      try {
        const data = await api(`/api/v1/projects/${projectId}/members/${userId}`, { method:"DELETE" });
        toast(`Acceso revocado · ${data.revoked_sessions.length} sesión(es) cerrada(s)`, "ok");
        await refreshProjects();
        renderEngineers();
      } catch (err) { toast(err.message, "bad"); }
    };
  });
}

async function createEngineer() {
  const email = $("eEmail").value.trim();
  const fullName = $("eName").value.trim();
  const password = $("ePassword").value;
  const invite = $("eInvite").value.trim();
  if (!email) { toast("Ingresa el correo del ingeniero", "bad"); return; }
  const selected = [];
  document.querySelectorAll("[data-eng-project]").forEach(row => {
    const chk = row.querySelector(".eng-project-check");
    if (!chk.checked) return;
    const skills = row.querySelector(".skills-input").value.split(",").map(s => s.trim()).filter(Boolean);
    selected.push({ id: row.dataset.engProject, skills });
  });
  if (!selected.length) { toast("Selecciona al menos un proyecto aprobado", "bad"); return; }
  const resultBox = $("engineerResult");
  resultBox.classList.remove("hidden");
  resultBox.textContent = "Procesando…";
  let firstPassword = null;
  try {
    for (const item of selected) {
      const body = { email, full_name: fullName || null, allowed_skills: item.skills };
      if (password) body.password = password;
      if (invite) body.invite_token = invite;
      const data = await api(`/api/v1/projects/${item.id}/members`, { method:"POST", body:JSON.stringify(body) });
      if (data.initial_password && !firstPassword) firstPassword = data.initial_password;
    }
    resultBox.innerHTML = `<b>Ingeniero aprobado en ${selected.length} proyecto(s).</b>` + (firstPassword ? `\nContraseña inicial generada: <span class="mono">${esc(firstPassword)}</span>\nGuárdala y compártela por un canal seguro.` : `\n${password || invite ? "Se usó la contraseña o invite token indicado." : "El usuario ya existía y conserva su credencial actual."}`);
    toast("Ingeniero creado / actualizado", "ok");
    await refreshProjects();
    renderEngineers();
  } catch (err) {
    resultBox.textContent = "Error: " + err.message;
    toast(err.message, "bad");
  }
}

function renderMyProjects() {
  const grid = $("myProjectsGrid");
  if (!grid) return;
  if (!state.projects.length) {
    grid.innerHTML = '<div class="card"><div class="muted">No tienes proyectos aprobados todavía. Pide al administrador que te asigne acceso.</div></div>';
    return;
  }
  grid.innerHTML = state.projects.map(p => {
    const sessions = (p.active_sessions || []);
    return `<article class="card project-card" data-project-card="${esc(p.id)}">
      <div class="row"><div style="flex:1;min-width:0"><h2>${esc(p.display_name)}</h2><div class="muted mono" style="font-size:12px">${esc(p.project_slug)}</div></div><span class="pill ok">APROBADO</span></div>
      <div class="muted" style="font-size:12.5px">${esc(p.description || "Sin descripción")}</div>
      <div class="meta"><span class="pill">${esc(p.default_branch || "main")}</span><span class="pill">${(p.allowed_skills || []).length} skills</span></div>
      <div class="skills">${(p.allowed_skills || []).map(s => `<span class="pill info">${esc(s)}</span>`).join("") || '<span class="muted" style="font-size:12px">Sin skills asignadas</span>'}</div>
      <div class="divider"></div>
      <div><div class="side-label" style="padding:0 0 6px">Sesiones activas</div>${sessions.length ? sessions.map(s => `<div class="row" style="justify-content:space-between;font-size:12px;padding:3px 0"><span>${esc(TOOL_LABELS[s.agent_tool] || s.agent_tool)} ${sessionPill(s.status)}</span><button class="btn sm" data-config-session="${esc(s.id)}" type="button">Configuración</button></div>`).join("") : '<div class="muted" style="font-size:12px">Sin sesión abierta en este proyecto.</div>'}</div>
      <div class="stack" style="gap:8px;margin-top:6px">
        <div class="grid-2">
          <div class="field"><label>Herramienta</label><select class="session-tool">${Object.entries(TOOL_LABELS).filter(([k]) => k !== "custom").map(([k,v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
          <div class="field"><label>Proveedor LLM</label><select class="session-provider"><option value="cloud-llm">Cloud (preferido)</option><option value="local-llm">IA Local</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="custom">Custom</option></select></div>
          <div class="field"><label>max_context_tokens</label><input class="session-max" type="number" min="1024" value="32000"></div>
        </div>
        <button class="btn primary start-session-btn" data-project="${esc(p.id)}" type="button">Iniciar / Configurar Mi Sesión en este Proyecto</button>
      </div>
      <div class="config-slot" data-config-slot="${esc(p.id)}"></div>
    </article>`;
  }).join("");
  grid.querySelectorAll(".start-session-btn").forEach(btn => { btn.onclick = () => startSession(btn.dataset.project, btn); });
  grid.querySelectorAll("[data-config-session]").forEach(btn => { btn.onclick = () => openSessionConfig(btn.dataset.configSession, btn); });
}

async function startSession(projectId, btn) {
  const card = btn.closest("[data-project-card]");
  const tool = card.querySelector(".session-tool").value;
  const provider = card.querySelector(".session-provider").value;
  const maxTokens = Number(card.querySelector(".session-max").value || 32000);
  btn.disabled = true;
  try {
    const session = await api("/api/v1/portal/sessions", {
      method:"POST",
      body:JSON.stringify({ project_id: projectId, agent_tool: tool, llm_provider: provider, max_context_tokens: maxTokens }),
    });
    state.sessionsByProject[projectId] = session;
    if (session.replaced_session_ids && session.replaced_session_ids.length) {
      toast("Sesión anterior rotada en este proyecto/herramienta", "ok");
    } else {
      toast("Sesión creada y vinculada al proyecto", "ok");
    }
    const bundle = await api(`/api/v1/portal/sessions/${session.session_id}/config-bundle`);
    state.bundles[session.session_id] = bundle;
    await refreshProjects();
    const slot = document.querySelector(`[data-project-card="${projectId}"] .config-slot`);
    renderConfig(session.session_id, bundle, slot);
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

async function openSessionConfig(sessionId, btn) {
  try {
    let bundle = state.bundles[sessionId];
    if (!bundle) {
      bundle = await api(`/api/v1/portal/sessions/${sessionId}/config-bundle`);
      state.bundles[sessionId] = bundle;
    }
    const slot = btn ? btn.closest("[data-project-card]").querySelector(".config-slot") : null;
    renderConfig(sessionId, bundle, slot);
    const target = slot || document.querySelector(".config-slot");
    if (target) target.scrollIntoView({ behavior:"smooth", block:"start" });
  } catch (err) {
    toast(err.message, "bad");
  }
}

function copyButton(targetId, label) {
  return `<button class="btn sm" type="button" data-copy-target="${esc(targetId)}">${esc(label)}</button>`;
}

function renderConfig(sessionId, bundle, slotEl) {
  let slot = slotEl || null;
  if (!slot) {
    document.querySelectorAll(".config-slot").forEach(s => {
      const card = s.closest("[data-project-card]");
      const sessionIdOnCard = [...card.querySelectorAll("[data-config-session]")].map(b => b.dataset.configSession);
      if (sessionIdOnCard.includes(sessionId)) slot = s;
    });
  }
  if (!slot) slot = document.querySelector(".config-slot");
  if (!slot) return;
  const env = bundle.env || {};
  slot.innerHTML = `<div class="card" style="margin-top:8px;border-color:rgba(56,189,248,.35)">
    <h3>Configuración habilitada · ${esc(bundle.project_slug)}</h3>
    <div class="muted" style="font-size:12px;margin-bottom:8px">Pega estos bloques en tu máquina. La session key está vinculada solo a este proyecto.</div>
    <div class="stack" style="gap:10px">
      <div><div class="row" style="justify-content:space-between"><label class="muted" style="font-size:12px">Cursor MCP JSON</label>${copyButton("cfg-cursor-" + sessionId, "Copiar JSON para Cursor")}</div><textarea id="cfg-cursor-${esc(sessionId)}" readonly>${esc(bundle.cursor_mcp_json)}</textarea></div>
      <div><div class="row" style="justify-content:space-between"><label class="muted" style="font-size:12px">Claude Code MCP JSON</label>${copyButton("cfg-claude-" + sessionId, "Copiar JSON para Claude")}</div><textarea id="cfg-claude-${esc(sessionId)}" readonly>${esc(bundle.claudecode_mcp_json)}</textarea></div>
      <div><div class="row" style="justify-content:space-between"><label class="muted" style="font-size:12px">OpenCode Config</label>${copyButton("cfg-opencode-" + sessionId, "Copiar Config OpenCode")}</div><textarea id="cfg-opencode-${esc(sessionId)}" readonly>${esc(bundle.opencode_json)}</textarea></div>
      <div><div class="row" style="justify-content:space-between"><label class="muted" style="font-size:12px">Hermes CLI env</label>${copyButton("cfg-hermes-" + sessionId, "Copiar Comando CLI Hermes")}</div><textarea id="cfg-hermes-${esc(sessionId)}" readonly>${esc(bundle.hermes_cli_env)}</textarea></div>
      <div><div class="row" style="justify-content:space-between"><label class="muted" style="font-size:12px">Descargar bridge</label>${copyButton("cfg-curl-" + sessionId, "Copiar curl")}</div><textarea id="cfg-curl-${esc(sessionId)}" readonly>${esc(bundle.bridge_download_command)}</textarea></div>
    </div>
    <div class="result-box" style="margin-top:9px">HARNESS_URL=${esc(env.HARNESS_URL || "")}\nHARNESS_SESSION_ID=${esc(env.HARNESS_SESSION_ID || "")}\nHARNESS_PROJECT_SLUG=${esc(env.HARNESS_PROJECT_SLUG || "")}\nLa session key se incluye en los bloques de arriba.</div>
  </div>`;
  slot.querySelectorAll("[data-copy-target]").forEach(btn => {
    btn.onclick = () => copyText($(btn.dataset.copyTarget).value, btn.textContent);
  });
}

async function copyText(text, label) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
    }
    toast((label || "Contenido") + " copiado", "ok");
  } catch (err) {
    toast("No se pudo copiar: " + err.message, "bad");
  }
}

function ensureWarRoom() {
  renderProjectChoosers();
  refreshTopology().catch(err => toast(err.message, "bad"));
  loadRepoIndex();
  connectWarRoom();
}

async function loadRepoIndex() {
  try {
    const data = await api("/api/v1/repos");
    const repos = data.repos || [];
    $("repo").innerHTML = repos.length
      ? repos.map(r => `<div>${esc(r.repo_name)} <span class="muted">· ${esc(r.files)} archivos · ${esc(String(r.last_commit || r.commit_sha || "").slice(0, 8))}</span></div>`).join("")
      : "sin repos indexados";
  } catch (err) {
    $("repo").textContent = "error: " + err.message;
  }
}

async function refreshTopology() {
  if (state.role !== "ADMIN") return;
  const data = await api("/api/v1/war-room/topology");
  state.topology = data;
  renderWarRoom();
}

function filteredTopology() {
  const t = state.topology || { nodes:[], links:[], tasks:[], locks:[], events:[] };
  const pid = state.projectFilter;
  const nodes = (t.nodes || []).filter(n => (pid === "all" || n.project_id === pid) && (n.status === "ONLINE" || n.status === "BUSY"));
  const ids = new Set(nodes.map(n => n.session_id));
  const links = (t.links || []).filter(l => ids.has(l.source_session_id) && ids.has(l.target_session_id));
  const tasks = (t.tasks || []).filter(x => pid === "all" || x.project_id === pid);
  const project = projectById(pid);
  const locks = (t.locks || []).filter(l => pid === "all" || (project && l.repo_name === project.project_slug));
  return { nodes, links, tasks, locks, events: t.events || [] };
}

function renderWarRoom() {
  if (state.role !== "ADMIN") return;
  const t = filteredTopology();
  renderGraph(t);
  renderWarSessions(t);
  renderWarTasks(t);
  renderWarLocks(t);
  renderWarInspector();
  renderLinkChoosers(t);
  $("graphCount").textContent = `${t.nodes.length} sesiones · ${t.tasks.length} tareas`;
  if (!state.selectedSession || !t.nodes.some(n => n.session_id === state.selectedSession)) {
    state.selectedSession = t.nodes[0] ? t.nodes[0].session_id : null;
  }
  renderWarInspector();
}

function renderGraph(t) {
  const svg = $("graph");
  svg.innerHTML = "";
  const W = 900, H = 560, cx = W / 2, cy = H / 2;
  const nodes = t.nodes;
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => { const el = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k, v)); return el; };
  const hub = mk("circle", { cx, cy, r:42, fill:"#0b1729", stroke:"#2b3d5f", "stroke-width":"1.5" });
  svg.appendChild(hub);
  const hubText = mk("text", { x:cx, y:cy + 4, fill:"#38bdf8", "font-size":"11", "font-weight":"800", "text-anchor":"middle" });
  hubText.textContent = "HARNESS";
  svg.appendChild(hubText);
  if (!nodes.length) {
    const empty = mk("text", { x:cx, y:cy + 90, fill:"#8fa3c4", "font-size":"14", "text-anchor":"middle" });
    empty.textContent = "Sin sesiones conectadas para este filtro";
    svg.appendChild(empty);
    return;
  }
  const R = Math.min(W, H) / 2 - 86;
  const pos = {};
  nodes.forEach((n, i) => {
    const a = (2 * Math.PI * i / nodes.length) - Math.PI / 2;
    pos[n.session_id] = { x:cx + R * Math.cos(a), y:cy + R * Math.sin(a) };
  });
  // Connectors from hub for every node
  nodes.forEach(n => {
    const p = pos[n.session_id];
    svg.appendChild(mk("line", { x1:p.x, y1:p.y, x2:cx, y2:cy, stroke:"#16233c", "stroke-width":"1" }));
  });
  t.links.forEach(l => {
    const a = pos[l.source_session_id], b = pos[l.target_session_id];
    if (!a || !b) return;
    const line = mk("line", { x1:a.x, y1:a.y, x2:b.x, y2:b.y, stroke:"#818cf8", "stroke-width":"2.2", opacity:"0.8" });
    svg.appendChild(line);
    const label = mk("text", { x:(a.x + b.x) / 2, y:(a.y + b.y) / 2 - 5, fill:"#8fa3c4", "font-size":"10", "text-anchor":"middle" });
    label.textContent = l.trigger_event || l.link_type;
    svg.appendChild(label);
  });
  nodes.forEach(n => {
    const p = pos[n.session_id];
    const color = STATUS_COLORS[n.status] || "#8fa3c4";
    const g = mk("g", { style:"cursor:pointer" });
    g.appendChild(mk("circle", { cx:p.x, cy:p.y, r: state.selectedSession === n.session_id ? 32 : 27, fill:"#0f1a2c", stroke:color, "stroke-width": state.selectedSession === n.session_id ? "4" : "2.4" }));
    const tool = mk("text", { x:p.x, y:p.y + 4, fill:color, "font-size":"10.5", "font-weight":"800", "text-anchor":"middle" });
    tool.textContent = String(TOOL_LABELS[n.agent_tool] || n.agent_tool || "?").slice(0, 11);
    g.appendChild(tool);
    const name = mk("text", { x:p.x, y:p.y + 45, fill:"#e6eefc", "font-size":"11", "text-anchor":"middle" });
    name.textContent = String(n.engineer_username || n.session_id.slice(0, 8));
    g.appendChild(name);
    const projectName = mk("text", { x:p.x, y:p.y + 59, fill:"#6f86ad", "font-size":"9", "text-anchor":"middle" });
    projectName.textContent = String(n.project_slug || "");
    g.appendChild(projectName);
    const pct = Math.max(0, Math.min(100, Number(n.context_pct || 0)));
    g.appendChild(mk("rect", { x:p.x - 45, y:p.y + 67, width:90, height:6, rx:3, fill:"#16233c" }));
    g.appendChild(mk("rect", { x:p.x - 45, y:p.y + 67, width:90 * pct / 100, height:6, rx:3, fill: pct >= 65 ? "#f87171" : pct >= 50 ? "#fbbf24" : "#34d399" }));
    const label = mk("text", { x:p.x, y:p.y + 86, fill:"#8fa3c4", "font-size":"9.5", "text-anchor":"middle" });
    label.textContent = `${n.status} · ${pct}%`;
    g.appendChild(label);
    const title = mk("title");
    title.textContent = `${n.engineer_username} · ${n.agent_tool} · ${n.project_slug || "sin proyecto"} · ${n.status}`;
    g.appendChild(title);
    g.addEventListener("click", () => { state.selectedSession = n.session_id; renderWarRoom(); });
    svg.appendChild(g);
  });
}

function renderWarSessions(t) {
  const box = $("warSessions");
  if (!t.nodes.length) { box.innerHTML = '<div class="muted" style="font-size:12px">Sin sesiones ONLINE/BUSY para este filtro.</div>'; return; }
  box.innerHTML = t.nodes.map(n => `<button class="session-item ${state.selectedSession === n.session_id ? "active" : ""}" data-session-id="${esc(n.session_id)}" type="button">
    <span class="avatar" style="width:28px;height:28px;background:${STATUS_COLORS[n.status] || "#8fa3c4"};color:#06101d">${esc(String(TOOL_LABELS[n.agent_tool] || "?").slice(0,2))}</span>
    <span class="who"><b>${esc(n.engineer_username || n.session_id.slice(0, 8))}</b><span>${esc(TOOL_LABELS[n.agent_tool] || n.agent_tool)} · ${esc(n.project_slug || "sin proyecto")}</span></span>
    ${sessionPill(n.status)}
  </button>`).join("");
  box.querySelectorAll("[data-session-id]").forEach(btn => {
    btn.onclick = () => { state.selectedSession = btn.dataset.sessionId; renderWarRoom(); };
  });
}

function renderWarTasks(t) {
  const box = $("warTasks");
  if (!t.tasks.length) { box.innerHTML = '<div class="muted" style="font-size:12px">Sin tareas pendientes o en curso.</div>'; return; }
  box.innerHTML = t.tasks.map(task => `<div class="task-card">
    <div class="top"><b>${esc(task.title)}</b><span class="pill ${task.status === "CONFLICT" ? "bad" : task.status === "IN_PROGRESS" ? "warn" : "info"}">${esc(task.status)}</span></div>
    <div class="meta"><span class="mono">${esc(task.project_slug || "")}</span><span>${esc(task.git_branch || "sin branch")}</span><span>${esc((task.required_skills || []).join(", ") || "sin skills")}</span></div>
    <div class="meta"><span>Asignada: ${esc(task.assigned_session_id ? task.assigned_session_id.slice(0, 8) : "—")}</span><span>Files: ${esc((task.allowed_files || []).join(", ") || "—")}</span></div>
  </div>`).join("");
}

function renderWarLocks(t) {
  const box = $("warLocks");
  if (!t.locks.length) { box.textContent = "sin locks"; return; }
  box.innerHTML = t.locks.map(l => `<div>${esc(l.repo_name)} :: ${esc(l.file_path)} <span class="muted">← ${esc((l.locked_by_task_id || "").slice(0, 8))}</span></div>`).join("");
}

function renderWarInspector() {
  const box = $("warInspector");
  const node = (state.topology && state.topology.nodes || []).find(n => n.session_id === state.selectedSession);
  if (!node) {
    box.innerHTML = '<div class="inspector-empty">Selecciona una sesión en el grafo o en la lista.</div>';
    $("cSkills").value = ""; $("cRules").value = ""; $("cMax").value = "";
    return;
  }
  box.innerHTML = `<div class="stack" style="gap:8px">
    <div class="row"><b style="flex:1">${esc(node.engineer_username)}</b>${sessionPill(node.status)}</div>
    <div class="muted mono" style="font-size:11px">${esc(node.session_id)}</div>
    <div class="row"><span class="pill">${esc(TOOL_LABELS[node.agent_tool] || node.agent_tool)}</span><span class="pill">${esc(node.llm_provider || "—")}</span><span class="pill info">${esc(node.project_slug || "sin proyecto")}</span></div>
    <div style="font-size:12px">Contexto: <b>${esc(node.current_context_tokens)}</b> / ${esc(node.max_context_tokens)} tokens</div>
    <div style="font-size:12px">Skills: ${(node.skills || []).map(s => `<span class="pill">${esc(s)}</span>`).join(" ") || '<span class="muted">—</span>'}</div>
    <div style="font-size:12px">Reglas: ${(node.system_contract_rules || []).map(s => `<span class="pill warn">${esc(s)}</span>`).join(" ") || '<span class="muted">—</span>'}</div>
    <div class="muted" style="font-size:11px">Último heartbeat: ${esc(node.last_heartbeat || "—")}</div>
  </div>`;
  $("cSkills").value = (node.skills || []).join(", ");
  $("cRules").value = (node.system_contract_rules || []).join(", ");
  $("cMax").value = node.max_context_tokens || "";
}

function renderLinkChoosers(t) {
  const opts = (t.nodes || []).map(n => `<option value="${esc(n.session_id)}">${esc(n.engineer_username)} · ${esc(TOOL_LABELS[n.agent_tool] || n.agent_tool)}</option>`).join("");
  const src = $("linkSrc"), tgt = $("linkTgt");
  const srcPrev = src.value, tgtPrev = tgt.value;
  src.innerHTML = opts; tgt.innerHTML = opts;
  if (srcPrev && [...src.options].some(o => o.value === srcPrev)) src.value = srcPrev;
  if (tgtPrev && [...tgt.options].some(o => o.value === tgtPrev)) tgt.value = tgtPrev;
}

function logConsole(kind, data) {
  const box = $("warConsole");
  const item = document.createElement("div");
  item.className = "ev";
  item.innerHTML = `<b>${esc(kind)}</b> <span class="muted">${esc(new Date().toLocaleTimeString())}</span>\n${esc(typeof data === "string" ? data : JSON.stringify(data, null, 2))}`;
  box.prepend(item);
  state.consoleLines += 1;
  while (box.children.length > 100) box.lastChild.remove();
}

function connectWarRoom() {
  if (state.role !== "ADMIN" || (!state.token && !state.adminKey)) return;
  if (state.ws && (state.ws.readyState === 0 || state.ws.readyState === 1)) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(proto + "//" + location.host + "/ws/war-room");
  state.ws = ws;
  $("warStatus").textContent = "WS: conectando…";
  $("warStatus").className = "pill warn";
  ws.onopen = () => {
    if (state.token) ws.send(JSON.stringify({ type:"AUTH", portal_token: state.token }));
    else ws.send(JSON.stringify({ type:"AUTH", admin_key: state.adminKey }));
  };
  ws.onmessage = (msg) => {
    let ev; try { ev = JSON.parse(msg.data); } catch { return; }
    if (ev.event === "WAR_ROOM_CONNECTED") {
      $("warStatus").textContent = "WS: conectado";
      $("warStatus").className = "pill ok";
      refreshTopology().catch(() => {});
      return;
    }
    logConsole(ev.event || "EVENT", ev.data ?? ev);
    if (["TASK_ASSIGNED","TASK_MERGED","TASK_CONFLICT","SKILLS_INJECTED","CONTROL_RULES_UPDATED","CONTEXT_UPDATE","PLAN_APPROVED","SESSION_REGISTERED","SESSION_OFFLINE","SESSION_REVOKED","AGENT_TO_AGENT_RELAY"].includes(ev.event)) {
      if (topoRefreshTimer) clearTimeout(topoRefreshTimer);
      topoRefreshTimer = setTimeout(() => refreshTopology().catch(() => {}), 700);
    }
  };
  ws.onclose = () => {
    $("warStatus").textContent = "WS: desconectado";
    $("warStatus").className = "pill bad";
    state.ws = null;
    if (state.role === "ADMIN" && (state.token || state.adminKey)) {
      if (wsRetry) clearTimeout(wsRetry);
      wsRetry = setTimeout(connectWarRoom, 3500);
    }
  };
  ws.onerror = () => { $("warStatus").textContent = "WS: error"; $("warStatus").className = "pill bad"; };
}

async function injectControl() {
  if (!state.selectedSession) { toast("Selecciona una sesión primero", "bad"); return; }
  const body = {};
  const skills = $("cSkills").value.split(",").map(s => s.trim()).filter(Boolean);
  const rules = $("cRules").value.split(",").map(s => s.trim()).filter(Boolean);
  if (skills.length) body.skills = skills;
  if (rules.length) body.system_contract_rules = rules;
  if ($("cMax").value) body.max_context_tokens = Number($("cMax").value);
  try {
    await api(`/api/v1/sessions/${state.selectedSession}/control`, { method:"PATCH", body:JSON.stringify(body) });
    toast("Skills y reglas inyectadas", "ok");
    await refreshTopology();
  } catch (err) { toast(err.message, "bad"); }
}

async function purgeContext() {
  if (!state.selectedSession) { toast("Selecciona una sesión primero", "bad"); return; }
  try {
    await api(`/api/v1/sessions/${state.selectedSession}/context-purged`, { method:"POST" });
    toast("Contexto purgado", "ok");
    await refreshTopology();
  } catch (err) { toast(err.message, "bad"); }
}

async function killSession() {
  if (!state.selectedSession) { toast("Selecciona una sesión primero", "bad"); return; }
  if (!confirm("¿Revocar esta sesión? El agente perderá acceso inmediatamente.")) return;
  try {
    await api(`/api/v1/sessions/${state.selectedSession}/revoke`, { method:"POST" });
    toast("Sesión revocada", "ok");
    await refreshTopology();
  } catch (err) { toast(err.message, "bad"); }
}

async function createLink() {
  const source = $("linkSrc").value, target = $("linkTgt").value;
  if (!source || !target) { toast("Selecciona origen y destino", "bad"); return; }
  try {
    await api("/api/v1/war-room/links", { method:"POST", body:JSON.stringify({
      source_session_id: source, target_session_id: target,
      link_type: $("linkType").value, trigger_event: $("linkTrigger").value,
    })});
    toast("Enlace creado", "ok");
    await refreshTopology();
  } catch (err) { toast(err.message, "bad"); }
}

async function proposePlan() {
  const project = projectById($("warPlanProject").value);
  const prompt = $("warPrompt").value.trim();
  if (!project) { toast("Selecciona un proyecto", "bad"); return; }
  if (prompt.length < 5) { toast("Describe el requerimiento", "bad"); return; }
  const box = $("planResult");
  box.classList.remove("hidden");
  box.textContent = "Generando Plan A/B/C…";
  try {
    const data = await api("/api/v1/plans/propose", { method:"POST", body:JSON.stringify({
      requested_by: state.user.email || "portal-admin",
      repo_name: project.project_slug,
      project_id: project.id,
      requirement_prompt: prompt,
      required_skills_hint: [],
    })});
    box.textContent = `Plan ${data.plan_id}\nTareas creadas: ${(data.created_tasks || []).length}\n` + (data.created_tasks || []).map(t => `- ${t.title}`).join("\n");
    logConsole("PLAN_PROPOSED", { plan_id:data.plan_id, tasks:data.created_tasks });
    await refreshTopology();
  } catch (err) {
    box.textContent = "Error: " + err.message;
    toast(err.message, "bad");
  }
}

async function dispatchTasks() {
  try {
    const data = await api("/api/v1/tasks/dispatch", { method:"POST" });
    logConsole("DISPATCH", data);
    toast(`Dispatch completado · ${data.count || 0} tarea(s)`, "ok");
    await refreshTopology();
  } catch (err) { toast(err.message, "bad"); }
}

function openProjectModal() {
  $("modalTitle").textContent = "Crear Nuevo Proyecto";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="grid-2">
      <div class="field"><label for="pSlug">project_slug</label><input id="pSlug" placeholder="proj-alpha"></div>
      <div class="field"><label for="pName">display_name</label><input id="pName" placeholder="Proyecto Alpha"></div>
    </div>
    <div class="field"><label for="pDesc">Descripción</label><textarea id="pDesc" rows="2" placeholder="Proyecto de ejemplo"></textarea></div>
    <div class="grid-2">
      <div class="field"><label for="pRepo">repo_url (opcional)</label><input id="pRepo" placeholder="https://github.com/org/repo.git"></div>
      <div class="field"><label for="pBranch">default_branch</label><input id="pBranch" value="main"></div>
    </div>
    <div class="login-note">Sin repo_url, el servidor crea el directorio, ejecuta git init -b main, crea README.md y el commit base, e indexa el proyecto en el acto.</div>
    <div id="projectFormMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="createProjectBtn" class="btn primary" type="button">Crear e Indexar</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("createProjectBtn").onclick = createProject;
  $("pSlug").focus();
}

function closeModal() { $("modal").classList.add("hidden"); }

async function createProject() {
  const body = {
    project_slug: $("pSlug").value.trim(),
    display_name: $("pName").value.trim(),
    description: $("pDesc").value.trim() || null,
    repo_url: $("pRepo").value.trim() || null,
    default_branch: $("pBranch").value.trim() || "main",
  };
  const msg = $("projectFormMsg");
  msg.classList.remove("hidden");
  msg.textContent = "Creando repositorio e indexando…";
  $("createProjectBtn").disabled = true;
  try {
    const data = await api("/api/v1/projects", { method:"POST", body:JSON.stringify(body) });
    msg.textContent = `Proyecto creado: ${data.project.project_slug}\nCommit: ${data.index.commit_sha}\nArchivos indexados: ${data.index.files_indexed}\nVectores AST: ${data.index.ast_vectors_created}`;
    toast("Proyecto creado e indexado", "ok");
    await refreshProjects();
    setTimeout(closeModal, 900);
  } catch (err) {
    msg.textContent = "Error: " + err.message;
    toast(err.message, "bad");
  } finally {
    $("createProjectBtn").disabled = false;
  }
}

function bindStaticEvents() {
  $("loginForm").addEventListener("submit", doEmailLogin);
  $("loginTabEmail").onclick = () => setLoginTab("email");
  $("loginTabKey").onclick = () => setLoginTab("key");
  $("adminKeyBtn").onclick = doKeyLogin;
  $("logoutBtn").onclick = logout;
  $("menuBtn").onclick = openDrawer;
  $("drawerBackdrop").onclick = closeDrawer;
  $("modalClose").onclick = closeModal;
  $("newProjectBtn").onclick = openProjectModal;
  $("createEngineerBtn").onclick = createEngineer;
  $("engSearch").oninput = renderEngineers;
  $("myRefresh").onclick = () => refreshProjects().catch(err => toast(err.message, "bad"));
  $("warRefresh").onclick = () => refreshTopology().catch(err => toast(err.message, "bad"));
  $("warFilter").onchange = () => { state.projectFilter = $("warFilter").value; renderWarRoom(); };
  $("injectBtn").onclick = injectControl;
  $("purgeBtn").onclick = purgeContext;
  $("killBtn").onclick = killSession;
  $("createLinkBtn").onclick = createLink;
  $("proposeBtn").onclick = proposePlan;
  $("dispatchBtn").onclick = dispatchTasks;
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closeDrawer(); } });
}

bindStaticEvents();
bootstrapToken();
</script>
</body>
</html>
"""
