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
          <div class="field"><label for="loginTotp">Código MFA <span class="muted">(operador de licencia)</span></label><input id="loginTotp" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"></div>
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
        <button id="passwordBtn" class="btn sm ghost" type="button">Mi clave</button>
        <button id="logoutBtn" class="btn sm ghost" type="button">Salir</button>
      </header>

      <div class="content">
        <section id="viewProjects" class="view">
          <div class="view-head">
            <div><h1>Proyectos</h1><p>Repositorios gobernados, miembros aprobados y sesiones activas.</p></div>
            <div class="spacer"></div>
            <button id="newProjectBtn" class="btn primary" type="button">+ Crear Nuevo Proyecto</button>
          </div>
          <div class="row"><input id="projectSearch" class="grow" placeholder="Filtrar proyectos por nombre o slug"></div>
          <div id="projectsGrid" class="project-grid"></div>
        </section>

        <section id="viewKnowledge" class="view hidden">
          <div class="view-head">
            <div><h1>Base de Conocimiento <span class="muted" style="font-size:14px">(RAG Docs)</span></h1><p>Documentación canónica indexada por proyecto, con búsqueda semántica y bootstrap del worker docs-curator (skill <code>rag-docs-curator</code>). Tools MCP: <code>harness_rag_list</code>, <code>harness_rag_search</code> y <code>harness_rag_ingest</code>.</p></div>
            <div class="spacer"></div>
            <button id="kbBootstrap" class="btn primary hidden" type="button">Ejecutar docs-curator</button>
          </div>
          <div class="card stack">
            <div class="row">
              <select id="kbProject" style="max-width:360px"></select>
              <button id="kbRefresh" class="btn sm" type="button">Refrescar</button>
              <span id="kbStatus" class="muted" style="font-size:12px"></span>
            </div>
            <div class="field">
              <label for="kbSearch">Buscar en la documentación del proyecto</label>
              <div class="row"><input id="kbSearch" class="grow" placeholder="Ej. arquitectura, contratos, API, runbook..."><button id="kbSearchBtn" class="btn sm" type="button">Buscar</button></div>
            </div>
            <div id="kbResults" class="stack" style="gap:8px"></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Fuente</th><th>Tipo</th><th>Título</th><th>Chunks</th><th>Actualizado</th></tr></thead>
                <tbody id="kbDocs"></tbody>
              </table>
            </div>
          </div>
        </section>

        <section id="viewSkills" class="view hidden">
          <div class="view-head">
            <div><h1>Skill Studio</h1><p>Habilidades con objetivo, pasos obligatorios, prohibiciones y criterio de éxito. El manual se carga solo cuando hace falta.</p></div>
            <div class="spacer"></div>
            <button id="newSkillBtn" class="btn primary hidden" type="button">+ Nueva Skill</button>
          </div>
          <div class="card stack">
            <div class="row">
              <input id="skillSearch" class="grow" placeholder="Buscar por nombre, slug o cuándo usarla">
              <select id="skillCategoryFilter" style="max-width:230px">
                <option value="all">Todas las áreas</option>
                <option value="backend">Backend / APIs</option>
                <option value="frontend">Frontend / UI</option>
                <option value="database">Base de datos</option>
                <option value="documents">Lectura de documentos</option>
                <option value="writing">Redacción / Documentación</option>
                <option value="qa">QA / Testing</option>
                <option value="devops">DevOps / Infraestructura</option>
                <option value="security">Seguridad</option>
                <option value="general">General</option>
              </select>
              <button id="skillsRefresh" class="btn sm" type="button">Refrescar</button>
              <span id="skillsCount" class="muted" style="font-size:12px"></span>
            </div>
            <div class="login-note">Revelación progresiva: el agente recibe solo el catálogo. El manual completo se inyecta para la tarea activa o se consulta con <span class="mono">harness_get_skill_manual</span>.</div>
            <div id="skillsGrid" class="project-grid"></div>
          </div>
        </section>

        <section id="viewEngineers" class="view hidden">
          <div class="view-head">
            <div><h1>Usuarios y Accesos</h1><p>Crea la cuenta primero y asígnale proyectos después. Sin sorpresas ni accesos automáticos.</p></div>
            <div class="spacer"></div>
            <button id="newUserBtn" class="btn primary" type="button">+ Crear Usuario</button>
          </div>
          <div class="card stack">
            <div class="row">
              <span class="pill info">1. Crear usuario</span>
              <span class="pill info">2. Asignar proyecto</span>
              <span class="pill info">3. Compartir credenciales</span>
            </div>
            <div class="row">
              <input id="userSearch" class="grow" placeholder="Buscar por correo o nombre">
              <button id="usersRefresh" class="btn sm" type="button">Refrescar</button>
              <span id="usersCount" class="muted" style="font-size:12px"></span>
            </div>
            <div class="login-note">Un usuario puede tener varios proyectos. Cada proyecto tendrá su propia sesión y configuración MCP independiente.</div>
            <div id="usersList" class="project-grid"></div>
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

        <section id="viewLicense" class="view hidden">
          <div class="view-head">
            <div><h1>Licencia y Pool Hermes</h1><p>Planes, cuotas atómicas y estado del pool de workers Hermes por proyecto.</p></div>
            <div class="spacer"></div>
            <span id="licenseOverLimit" class="pill bad hidden">Over limit</span>
          </div>
          <div id="licenseGovernanceNote" class="card" style="padding:12px 14px;font-size:13px"></div>
          <div class="card stack">
            <div class="card" style="padding:14px">
              <div class="row"><span id="licenseSignedStatus" class="pill">MISSING</span><b id="licenseSignedCustomer" style="flex:1"></b><span id="licenseAuditState" class="pill">Audit —</span></div>
              <div id="licenseSignedMeta" class="muted" style="font-size:12px;margin-top:6px"></div>
              <div class="row" style="margin-top:8px"><span id="licenseActivationStatus" class="pill">Activación —</span><span id="licenseActivationMeta" class="muted" style="font-size:12px;flex:1"></span><button id="licenseActivate" class="btn sm" type="button">Activar / Renovar</button></div>
            </div>
            <div class="row">
              <div class="field grow"><label for="licensePlan">Plan activo</label><select id="licensePlan"></select></div>
              <button id="licenseApply" class="btn primary" type="button">Aplicar plan / cuotas</button>
              <button id="licenseRefresh" class="btn sm" type="button">Refrescar</button>
            </div>
            <div class="grid-2">
              <div class="field"><label for="licenseActiveProjects">Máx. proyectos activos</label><input id="licenseActiveProjects" type="number" min="0"></div>
              <div class="field"><label for="licenseHermesSessions">Máx. sesiones Hermes</label><input id="licenseHermesSessions" type="number" min="0"></div>
              <div class="field"><label for="licenseEngineersProject">Máx. ingenieros por proyecto</label><input id="licenseEngineersProject" type="number" min="0"></div>
              <div class="field"><label for="licenseEngineersTotal">Máx. sesiones de ingeniero globales</label><input id="licenseEngineersTotal" type="number" min="0"></div>
              <div class="field"><label for="licenseUsers">Máx. usuarios registrados</label><input id="licenseUsers" type="number" min="0"></div>
              <div class="field"><label for="licenseParallelJobs">Jobs RAG en paralelo</label><input id="licenseParallelJobs" type="number" min="1"></div>
            </div>
            <div id="licenseUsage" class="project-grid"></div>
            <div class="card" style="padding:14px">
              <div class="side-label">Jobs Hermes recientes</div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Proyecto</th><th>Tipo</th><th>Estado</th><th>Intentos</th><th>Creado</th><th>Error</th></tr></thead>
                  <tbody id="licenseJobs"></tbody>
                </table>
              </div>
            </div>
            <div class="card" style="padding:14px">
              <div class="side-label">Auditoría de licencia (hash chain)</div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>#</th><th>Evento</th><th>Actor</th><th>Hash</th><th>Fecha</th></tr></thead>
                  <tbody id="licenseAuditRows"></tbody>
                </table>
              </div>
            </div>
            <div class="muted" id="licenseStatus" style="font-size:12px"></div>
          </div>
        </section>

        <section id="viewSmtp" class="view hidden">
          <div class="view-head">
            <div><h1>SMTP y Notificaciones</h1><p>Configura el correo transaccional y verifica las credenciales antes de invitar ingenieros.</p></div>
            <div class="spacer"></div>
            <span id="smtpPasswordState" class="pill">Sin password</span>
          </div>
          <div class="grid-2" style="align-items:start">
            <div class="card stack">
              <h3>Configuración SMTP</h3>
              <div class="form-grid">
                <div class="field"><label for="smtpHost">Host</label><input id="smtpHost" placeholder="smtp.gmail.com"></div>
                <div class="field"><label for="smtpPort">Puerto</label><input id="smtpPort" type="number" min="1" max="65535" value="587"></div>
                <div class="field"><label for="smtpUser">Usuario</label><input id="smtpUser" autocomplete="off" placeholder="cuenta@gmail.com"></div>
                <div class="field"><label for="smtpPass">Contraseña / App password</label><input id="smtpPass" type="password" autocomplete="new-password" placeholder="Dejar vacío para conservar"></div>
                <div class="field"><label for="smtpFrom">From email</label><input id="smtpFrom" type="email" placeholder="harness-bot@controlplane.local"></div>
                <div class="field"><label for="smtpTestTo">Test recipient</label><input id="smtpTestTo" type="email" placeholder="admin@empresa.com"></div>
              </div>
              <div class="row">
                <label class="check-row" style="flex:1"><input id="smtpTls" type="checkbox" checked><span>TLS (587)</span></label>
                <label class="check-row" style="flex:1"><input id="smtpSsl" type="checkbox"><span>SSL (465)</span></label>
              </div>
              <div class="row">
                <button id="smtpSave" class="btn" type="button">Guardar Configuración</button>
                <button id="smtpSaveTest" class="btn primary" type="button">Guardar y Enviar Test</button>
                <button id="smtpTest" class="btn" type="button">Solo Enviar Test</button>
              </div>
              <div id="smtpStatus" class="result-box hidden"></div>
              <div class="login-note">La contraseña se guarda cifrada en el vault del servidor y nunca se devuelve al navegador.</div>
            </div>
            <div class="card stack">
              <h3>Auditoría de correos</h3>
              <div class="row"><button id="smtpLogsRefresh" class="btn sm" type="button">Refrescar</button><span id="smtpLogsCount" class="muted" style="font-size:12px"></span></div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Destinatario</th><th>Plantilla</th><th>Asunto</th><th>Estado</th><th>Enviado</th></tr></thead>
                  <tbody id="smtpLogsBody"></tbody>
                </table>
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
const TOOL_LABELS = { opencode:"OpenCode", claudecode:"Claude Code", cursor:"Cursor", hermes:"Hermes", pi:"Pi", custom:"Custom", "headless-worker":"Docs Curator" };
const SKILL_CATEGORY_LABELS = { backend:"Backend / APIs", frontend:"Frontend / UI", database:"Base de datos", documents:"Documentos", writing:"Redacción", qa:"QA / Testing", devops:"DevOps", security:"Seguridad", general:"General" };
const state = {
  token: null, adminKey: null, user: null, role: null,
  projects: [], topology: null, activeView: "projects",
  adminUsers: [], skills: [], skillViewer: null, skillFilter: "",
  editingSkillSlug: null, skillPreviewTimer: null,
  assignProjectId: null, assignUserId: null, assignSelectedSkills: new Set(),
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
  if (state.role === "LICENSE_ADMIN") {
    return [
      { id:"license", icon:"▣", label:"Licencia y Pool", bottom:"Licencia" },
    ];
  }
  if (state.role === "ADMIN") {
    return [
      { id:"projects", icon:"▦", label:"Proyectos", bottom:"Proyectos" },
      { id:"knowledge", icon:"▤", label:"Base de Conocimiento", bottom:"Docs" },
      { id:"skills", icon:"✦", label:"Skill Studio", bottom:"Skills" },
      { id:"engineers", icon:"☺", label:"Usuarios y Accesos", bottom:"Usuarios" },
      { id:"license", icon:"▣", label:"Licencia y Pool", bottom:"Licencia" },
      { id:"warroom", icon:"◎", label:"Sala de Control", bottom:"War Room" },
      { id:"smtp", icon:"✉", label:"SMTP y Correos", bottom:"SMTP" },
    ];
  }
  return [
    { id:"myprojects", icon:"▤", label:"Mis Proyectos", bottom:"Proyectos" },
    { id:"knowledge", icon:"▤", label:"Base de Conocimiento", bottom:"Docs" },
    { id:"skills", icon:"✦", label:"Skill Studio", bottom:"Skills" },
  ];
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
  const views = { projects:"viewProjects", knowledge:"viewKnowledge", skills:"viewSkills", engineers:"viewEngineers", license:"viewLicense", warroom:"viewWarRoom", smtp:"viewSmtp", myprojects:"viewMyProjects" };
  Object.values(views).forEach(id => $(id).classList.add("hidden"));
  if (views[name]) $(views[name]).classList.remove("hidden");
  document.querySelectorAll("[data-view]").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
  $("topTitle").textContent = ({projects:"Proyectos", knowledge:"Base de Conocimiento", skills:"Skill Studio", engineers:"Usuarios y Accesos", license:"Licencia y Pool Hermes", warroom:"Sala de Control", smtp:"SMTP y Correos", myprojects:"Mis Proyectos"})[name] || "Harness";
  closeDrawer();
  if (name === "warroom") { if (state.role === "ADMIN") { ensureWarRoom(); } }
  if (name === "knowledge") loadKnowledge();
  if (name === "skills") loadSkills();
  if (name === "engineers") loadAdminUsers();
  if (name === "license") loadLicense();
  if (name === "projects") renderProjects();
  if (name === "smtp") { loadSmtpConfig(); loadEmailLogs(); }
  if (name === "myprojects") renderMyProjects();
}

function openDrawer() { $("sidebar").classList.add("open"); $("drawerBackdrop").classList.add("open"); }
function closeDrawer() { $("sidebar").classList.remove("open"); $("drawerBackdrop").classList.remove("open"); }

async function doEmailLogin(event) {
  event.preventDefault();
  const email = $("loginEmail").value.trim();
  const password = $("loginPassword").value;
  const totpCode = $("loginTotp").value.trim();
  if (!email || !password) { showLogin("Completa email y contraseña."); return; }
  $("loginBtn").disabled = true;
  try {
    const body = { email, password };
    if (totpCode) body.totp_code = totpCode;
    const data = await api("/api/v1/auth/login", { method:"POST", body:JSON.stringify(body) });
    state.token = data.access_token;
    state.adminKey = null;
    state.user = data.user;
    state.role = data.user.role;
    sessionStorage.setItem("harness.token", state.token);
    $("loginPassword").value = "";
    $("loginTotp").value = "";
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
  $("passwordBtn").classList.toggle("hidden", !state.token || state.role === "LICENSE_ADMIN");
  $("logoutBtn").classList.remove("hidden");
  buildNav();
  if (state.role === "LICENSE_ADMIN") {
    setView("license");
  } else {
    await refreshProjects();
    if (state.role === "ADMIN") {
      $("viewProjects").classList.remove("hidden");
      setView("projects");
    } else {
      setView("myprojects");
    }
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
  const q = ($("projectSearch") ? $("projectSearch").value : "").toLowerCase();
  const projects = state.projects.filter(p =>
    !q || p.display_name.toLowerCase().includes(q) || p.project_slug.toLowerCase().includes(q)
  );
  if (!projects.length) {
    grid.innerHTML = state.projects.length
      ? '<div class="card"><div class="muted">Ningún proyecto coincide con el filtro.</div></div>'
      : '<div class="card"><div class="muted">Aún no hay proyectos. Crea el primero para inicializar git, el README base y el índice AST.</div></div>';
    return;
  }
  grid.innerHTML = projects.map(p => {
    const members = p.members || [];
    const sessions = p.active_sessions || [];
    const worker = p.hermes_worker;
    return `<article class="card project-card">
      <div class="row"><div style="flex:1;min-width:0"><h2>${esc(p.display_name)}</h2><div class="muted mono" style="font-size:12px">${esc(p.project_slug)}</div></div><span class="pill ${p.is_active_slot ? "ok" : "bad"}">${p.is_active_slot ? "Slot activo" : "Slot inactivo"}</span><span class="pill ${sessions.length ? "ok" : ""}">${sessions.length} sesiones activas</span></div>
      <div class="muted" style="font-size:12.5px">${esc(p.description || "Sin descripción")}</div>
      <div class="meta"><span class="pill">${esc(p.default_branch || "main")}</span>${p.repo_url ? `<span class="pill info">repo remoto</span>` : `<span class="pill">repo local</span>`}<span class="pill">${members.length} miembros</span>${worker ? `<span class="pill ${worker.status === "ONLINE" || worker.status === "BUSY" ? "ok" : "warn"}">Hermes ${esc(worker.status)}</span>` : '<span class="pill">Hermes sin worker</span>'}</div>
      <div class="divider"></div>
      <div><div class="side-label" style="padding:0 0 6px">Miembros aprobados</div>${members.length ? members.map(m => `<div class="row" style="justify-content:space-between;font-size:12px;padding:2px 0"><span>${esc(m.email)}</span><span class="muted">${esc((m.allowed_skills || []).join(", ") || "sin skills")}</span></div>`).join("") : '<div class="muted" style="font-size:12px">Sin miembros</div>'}</div>
      <div class="row" style="margin-top:auto"><button class="btn sm" data-war-pid="${esc(p.id)}" type="button">Abrir War Room</button><button class="btn sm" data-toggle-slot="${esc(p.id)}" data-active="${p.is_active_slot ? "false" : "true"}" type="button">${p.is_active_slot ? "Desactivar slot" : "Activar slot"}</button></div>
    </article>`;
  }).join("");
  grid.querySelectorAll("[data-war-pid]").forEach(btn => {
    btn.onclick = () => { state.projectFilter = btn.dataset.warPid; setView("warroom"); };
  });
  grid.querySelectorAll("[data-toggle-slot]").forEach(btn => {
    btn.onclick = () => toggleProjectSlot(btn.dataset.toggleSlot, btn.dataset.active === "true");
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

async function loadAdminUsers() {
  try {
    const data = await api("/api/v1/admin/users");
    state.adminUsers = data.users || [];
    renderAdminUsers();
  } catch (err) {
    toast(err.message, "bad");
  }
}

function renderAdminUsers() {
  const box = $("usersList");
  if (!box) return;
  const q = ($("userSearch").value || "").toLowerCase();
  const users = (state.adminUsers || []).filter(u =>
    !q || (u.email || "").toLowerCase().includes(q) || (u.full_name || "").toLowerCase().includes(q)
  );
  $("usersCount").textContent = `${users.length} usuario(s)`;
  if (!users.length) {
    box.innerHTML = '<div class="card"><div class="muted">No hay usuarios que coincidan. Usa “+ Crear Usuario”.</div></div>';
    return;
  }
  box.innerHTML = users.map(u => {
    const memberships = u.memberships || [];
    const approved = memberships.filter(m => m.status === "APPROVED");
    const membershipRows = memberships.length ? memberships.map(m => `<div class="row" style="justify-content:space-between;gap:8px;padding:6px 0;border-top:1px solid var(--line)">
      <div style="min-width:0">
        <span class="mono" style="font-size:12px">${esc(m.project_slug)}</span>
        <span class="pill ${m.status === "APPROVED" ? "ok" : "bad"}">${esc(m.status)}</span>
        <div class="muted" style="font-size:11px">${esc((m.allowed_skills || []).join(", ") || "sin skills")}</div>
      </div>
      ${m.status === "APPROVED" ? `<button class="btn sm danger" data-revoke-project="${esc(m.project_id)}" data-revoke-user="${esc(u.id)}" type="button">Revocar</button>` : ""}
    </div>`).join("") : '<div class="muted" style="font-size:12px">Sin proyectos asignados. Usa “Asignar proyecto”.</div>';
    return `<article class="card project-card">
      <div class="row"><div style="flex:1;min-width:0"><h2>${esc(u.full_name || u.email)}</h2><div class="muted mono" style="font-size:12px">${esc(u.email)}</div></div>${u.role === "ADMIN" ? '<span class="pill info">ADMIN</span>' : '<span class="pill">ENGINEER</span>'}${u.status === "ACTIVE" ? '<span class="pill ok">ACTIVE</span>' : `<span class="pill warn">${esc(u.status)}</span>`}</div>
      <div class="meta"><span class="pill">${approved.length} proyecto(s)</span><span class="pill">${u.active_sessions || 0} sesión(es)</span></div>
      <div>${membershipRows}</div>
      <div class="row" style="margin-top:auto">
        <button class="btn sm primary" data-assign-user="${esc(u.id)}" data-assign-email="${esc(u.email)}" type="button">Asignar proyecto</button>
        <button class="btn sm" data-caps-user="${esc(u.id)}" data-caps-email="${esc(u.email)}" type="button">Permisos skills</button>
        <button class="btn sm" data-reset-user="${esc(u.id)}" data-reset-email="${esc(u.email)}" type="button">Reset password</button>
      </div>
    </article>`;
  }).join("");
  box.querySelectorAll("[data-assign-user]").forEach(btn => {
    btn.onclick = () => openAssignProjectModal(btn.dataset.assignUser, btn.dataset.assignEmail);
  });
  box.querySelectorAll("[data-caps-user]").forEach(btn => {
    btn.onclick = () => openUserCapabilitiesModal(btn.dataset.capsUser, btn.dataset.capsEmail);
  });
  box.querySelectorAll("[data-reset-user]").forEach(btn => {
    btn.onclick = () => openAdminResetModal(btn.dataset.resetUser, btn.dataset.resetEmail);
  });
  box.querySelectorAll("[data-revoke-project]").forEach(btn => {
    btn.onclick = async () => {
      if (!confirm("¿Revocar este proyecto y cerrar las sesiones del usuario en él?")) return;
      try {
        const data = await api(`/api/v1/projects/${btn.dataset.revokeProject}/members/${btn.dataset.revokeUser}`, { method:"DELETE" });
        toast(`Acceso revocado · ${data.revoked_sessions.length} sesión(es) cerrada(s)`, "ok");
        await loadAdminUsers();
        await refreshProjects();
      } catch (err) { toast(err.message, "bad"); }
    };
  });
}

function showNewUserMsg(text, ok) {
  const box = $("newUserMsg");
  box.classList.remove("hidden");
  box.textContent = text;
  box.style.borderColor = ok ? "rgba(52,211,153,.45)" : "rgba(248,113,113,.45)";
}

function openCreateUserModal() {
  $("modalTitle").textContent = "Crear usuario";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="result-box">Paso 1: crea la cuenta. Paso 2: usa “Asignar proyecto” en su tarjeta. El usuario no se asigna automáticamente a ningún proyecto.</div>
    <div class="form-grid">
      <div class="field"><label for="newUserEmail">Email</label><input id="newUserEmail" type="email" placeholder="dev1@empresa.com"></div>
      <div class="field"><label for="newUserName">Nombre completo</label><input id="newUserName" placeholder="Dev Uno"></div>
      <div class="field"><label for="newUserPassword">Contraseña inicial (opcional)</label><input id="newUserPassword" type="password" autocomplete="new-password" placeholder="Si se omite, se genera"></div>
      <div class="field"><label for="newUserInvite">Invite token (opcional)</label><input id="newUserInvite" placeholder="token de invitación"></div>
    </div>
    <div class="login-note">Si dejas contraseña e invite token vacíos, se genera una temporal y se envía por correo. La política exige 8+ caracteres, mayúscula, minúscula, número y símbolo.</div>
    <div id="newUserMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="newUserSave" class="btn primary" type="button">Crear usuario</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("newUserSave").onclick = submitCreateUser;
  $("newUserEmail").focus();
}

async function submitCreateUser() {
  const email = $("newUserEmail").value.trim();
  const fullName = $("newUserName").value.trim();
  const password = $("newUserPassword").value;
  const invite = $("newUserInvite").value.trim();
  if (!email || !fullName) { showNewUserMsg("Completa email y nombre", false); return; }
  if (password && invite) { showNewUserMsg("Usa contraseña o invite token, no ambos", false); return; }
  const body = { email, full_name: fullName };
  if (password) body.password = password;
  if (invite) body.invite_token = invite;
  $("newUserSave").disabled = true;
  try {
    const data = await api("/api/v1/admin/users", { method:"POST", body:JSON.stringify(body) });
    const detail = data.generated
      ? "Contraseña temporal generada y enviada por correo."
      : "Credencial configurada.";
    showNewUserMsg(`Usuario creado sin proyectos. ${detail} Correo: ${data.email_sent ? "SENT" : "FAILED"}.`, data.email_sent);
    toast(data.email_sent ? "Usuario creado y correo de bienvenida enviado" : "Usuario creado; el correo fallo", data.email_sent ? "ok" : "bad");
    await loadAdminUsers();
    if (data.email_sent) setTimeout(closeModal, 1200);
  } catch (err) {
    showNewUserMsg("Error: " + err.message, false);
  } finally {
    $("newUserSave").disabled = false;
  }
}

async function openAssignProjectModal(userId, email) {
  if (!state.projects.length) {
    toast("Primero crea un proyecto en la pestaña Proyectos", "bad");
    return;
  }
  if (!state.skills.length) {
    try { await loadSkills(); } catch (err) { toast(err.message, "bad"); }
  }
  state.assignUserId = userId;
  state.assignProjectId = null;
  state.assignSelectedSkills = new Set();
  $("modalTitle").textContent = "Asignar proyecto";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="result-box">Usuario: <b>${esc(email)}</b><br>Primero elige el proyecto. Después marca las skills que puede usar en ese proyecto.</div>
    <div class="field">
      <label for="assignProjectSearch">Buscar proyecto</label>
      <input id="assignProjectSearch" placeholder="Filtra por nombre o slug..." autocomplete="off">
      <div id="assignProjectList" class="stack" style="gap:6px;max-height:210px;overflow:auto;margin-top:4px"></div>
    </div>
    <div class="field">
      <label for="assignSkillSearch">Skills permitidas en este proyecto</label>
      <input id="assignSkillSearch" placeholder="Buscar skill por nombre, categoría o slug..." autocomplete="off">
      <div id="assignSkillList" class="stack" style="gap:6px;max-height:230px;overflow:auto;margin-top:4px"></div>
    </div>
    <div class="login-note">Solo lectura = skill oficial que no se puede modificar. El agente recibirá el manual únicamente cuando la tarea lo requiera.</div>
    <div id="assignMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="assignSave" class="btn primary" type="button">Asignar proyecto</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("assignSave").onclick = () => submitAssignProject(userId);
  $("assignProjectSearch").oninput = renderAssignProjects;
  $("assignSkillSearch").oninput = renderAssignSkills;
  renderAssignProjects();
  renderAssignSkills();
  $("assignProjectSearch").focus();
}

function renderAssignProjects() {
  const box = $("assignProjectList");
  if (!box) return;
  const q = ($("assignProjectSearch").value || "").toLowerCase();
  const projects = state.projects.filter(p =>
    !q || p.display_name.toLowerCase().includes(q) || p.project_slug.toLowerCase().includes(q)
  );
  box.innerHTML = projects.length ? projects.map(p => `<label class="check-row" style="cursor:pointer">
    <input type="radio" name="assignProject" value="${esc(p.id)}" ${state.assignProjectId === p.id ? "checked" : ""}>
    <div class="grow"><b>${esc(p.display_name)}</b><div class="muted mono" style="font-size:11px">${esc(p.project_slug)}</div></div>
  </label>`).join("") : '<div class="muted" style="font-size:12px">Ningún proyecto coincide con el filtro.</div>';
  box.querySelectorAll('input[name="assignProject"]').forEach(radio => {
    radio.onchange = () => {
      state.assignProjectId = radio.value;
      const user = (state.adminUsers || []).find(u => u.id === state.assignUserId);
      const membership = ((user && user.memberships) || []).find(m => m.project_id === radio.value && m.status === "APPROVED");
      state.assignSelectedSkills = new Set((membership && membership.allowed_skills) || []);
      renderAssignSkills();
    };
  });
}

function renderAssignSkills() {
  const box = $("assignSkillList");
  if (!box) return;
  if (!state.assignProjectId) {
    box.innerHTML = '<div class="muted" style="font-size:12px">Selecciona un proyecto para ver y asignar skills.</div>';
    return;
  }
  const q = ($("assignSkillSearch").value || "").toLowerCase();
  const skills = (state.skills || []).filter(s => {
    const hay = `${s.display_name} ${s.slug} ${s.category} ${s.trigger_description}`.toLowerCase();
    return !q || hay.includes(q);
  });
  box.innerHTML = skills.length ? skills.map(s => `<label class="check-row" style="cursor:pointer">
    <input type="checkbox" class="assign-skill-check" value="${esc(s.slug)}" ${state.assignSelectedSkills.has(s.slug) ? "checked" : ""}>
    <div class="grow">
      <div class="row" style="gap:6px"><b>${esc(s.display_name)}</b><span class="pill">${esc(SKILL_CATEGORY_LABELS[s.category] || s.category)}</span>${s.is_official ? '<span class="pill info">Oficial · solo lectura</span>' : ""}</div>
      <div class="muted" style="font-size:11px">${esc(s.trigger_description)}</div>
    </div>
  </label>`).join("") : '<div class="muted" style="font-size:12px">No hay skills en el catálogo. Crea una en Skill Studio.</div>';
  box.querySelectorAll(".assign-skill-check").forEach(chk => {
    chk.onchange = () => {
      if (chk.checked) state.assignSelectedSkills.add(chk.value);
      else state.assignSelectedSkills.delete(chk.value);
    };
  });
}

async function submitAssignProject(userId) {
  if (!state.assignProjectId) {
    toast("Selecciona un proyecto de la lista", "bad");
    return;
  }
  const skills = Array.from(state.assignSelectedSkills);
  $("assignSave").disabled = true;
  try {
    await api(`/api/v1/projects/${state.assignProjectId}/members`, {
      method:"POST", body:JSON.stringify({ user_id: userId, allowed_skills: skills }),
    });
    toast(`Proyecto asignado con ${skills.length} skill(s). Correo en cola.`, "ok");
    await loadAdminUsers();
    await refreshProjects();
    closeModal();
  } catch (err) {
    const box = $("assignMsg");
    box.classList.remove("hidden");
    box.textContent = "Error: " + err.message;
    box.style.borderColor = "rgba(248,113,113,.45)";
  } finally {
    $("assignSave").disabled = false;
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
  const mk = (tag, attrs = {}) => { const el = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k, v)); return el; };
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
    ${n.is_headless_worker ? '<span class="pill info">DOC</span>' : ""}
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
    <div class="row"><b style="flex:1">${esc(node.engineer_username)}</b>${node.is_headless_worker ? '<span class="pill info">HEADLESS DOC</span>' : ""}${sessionPill(node.status)}</div>
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

function showSmtpStatus(text, ok) {
  const box = $("smtpStatus");
  box.classList.remove("hidden");
  box.textContent = text;
  box.style.borderColor = ok ? "rgba(52,211,153,.45)" : "rgba(248,113,113,.45)";
}

function smtpFormBody(withTest) {
  const body = {
    host: $("smtpHost").value.trim(),
    port: Number($("smtpPort").value || 587),
    username: $("smtpUser").value.trim() || null,
    from_email: $("smtpFrom").value.trim(),
    use_tls: $("smtpTls").checked,
    use_ssl: $("smtpSsl").checked,
  };
  const password = $("smtpPass").value;
  if (password) body.password = password;
  if (withTest) {
    const recipient = ($("smtpTestTo").value.trim() || (state.user && state.user.email) || "").trim();
    if (!recipient || recipient === "admin-key") throw new Error("Indica un Test recipient valido");
    body.test_recipient = recipient;
  }
  return body;
}

async function loadSmtpConfig() {
  try {
    const cfg = await api("/api/v1/admin/smtp-config");
    $("smtpHost").value = cfg.host || "";
    $("smtpPort").value = cfg.port || 587;
    $("smtpUser").value = cfg.username || "";
    $("smtpPass").value = "";
    $("smtpFrom").value = cfg.from_email || "";
    $("smtpTls").checked = !!cfg.use_tls;
    $("smtpSsl").checked = !!cfg.use_ssl;
    $("smtpPasswordState").textContent = cfg.password_set ? "Password configurado" : "Sin password";
    $("smtpPasswordState").className = "pill " + (cfg.password_set ? "ok" : "warn");
  } catch (err) {
    toast(err.message, "bad");
  }
}

async function saveSmtp(withTest) {
  let body;
  try { body = smtpFormBody(withTest); } catch (err) { toast(err.message, "bad"); return; }
  const buttons = [$("smtpSave"), $("smtpSaveTest")];
  buttons.forEach(b => b.disabled = true);
  try {
    const cfg = await api("/api/v1/admin/smtp-config", { method:"PUT", body:JSON.stringify(body) });
    if (withTest) {
      showSmtpStatus("Configuracion guardada. Test email: " + (cfg.test_sent ? "SENT" : "FAILED (revisa la auditoria)"), !!cfg.test_sent);
      toast(cfg.test_sent ? "Configuracion guardada y correo de prueba enviado" : "Configuracion guardada; el test fallo", cfg.test_sent ? "ok" : "bad");
    } else {
      showSmtpStatus("Configuracion SMTP guardada.", true);
      toast("Configuracion SMTP guardada", "ok");
    }
    $("smtpPass").value = "";
    await loadSmtpConfig();
    await loadEmailLogs();
  } catch (err) {
    showSmtpStatus("Error: " + err.message, false);
    toast(err.message, "bad");
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}

async function sendSmtpTest() {
  const recipient = ($("smtpTestTo").value.trim() || (state.user && state.user.email) || "").trim();
  if (!recipient || recipient === "admin-key") { toast("Indica un Test recipient valido", "bad"); return; }
  $("smtpTest").disabled = true;
  try {
    const data = await api("/api/v1/admin/smtp-config/test", { method:"POST", body:JSON.stringify({ recipient }) });
    showSmtpStatus("Correo de prueba a " + data.recipient + ": " + (data.sent ? "SENT" : "FAILED"), !!data.sent);
    toast(data.sent ? "Correo de prueba enviado" : "El correo de prueba fallo", data.sent ? "ok" : "bad");
    await loadEmailLogs();
  } catch (err) {
    showSmtpStatus("Error: " + err.message, false);
    toast(err.message, "bad");
  } finally {
    $("smtpTest").disabled = false;
  }
}

async function loadEmailLogs() {
  try {
    const data = await api("/api/v1/admin/email-logs?limit=50");
    const logs = data.logs || [];
    $("smtpLogsCount").textContent = logs.length + " registro(s)";
    $("smtpLogsBody").innerHTML = logs.length ? logs.map(l => `<tr>
      <td><b>${esc(l.recipient)}</b></td>
      <td><span class="pill info">${esc(l.template_name)}</span></td>
      <td title="${esc(l.error_message || "")}">${esc(l.subject)}</td>
      <td><span class="pill ${l.status === "SENT" ? "ok" : "bad"}">${esc(l.status)}</span></td>
      <td class="muted" style="font-size:12px">${esc(l.sent_at ? new Date(l.sent_at).toLocaleString() : "—")}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="muted">Sin correos registrados.</td></tr>`;
  } catch (err) {
    toast(err.message, "bad");
  }
}

function passwordPolicyMessage(password) {
  if (!password || password.length < 8) return "Minimo 8 caracteres";
  if (!/[A-Z]/.test(password)) return "Falta una mayuscula";
  if (!/[a-z]/.test(password)) return "Falta una minuscula";
  if (!/[0-9]/.test(password)) return "Falta un numero";
  if (!/[^A-Za-z0-9]/.test(password)) return "Falta un simbolo";
  return "";
}

function openSelfPasswordModal() {
  $("modalTitle").textContent = "Cambiar mi contrasena";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="field"><label for="pwdCurrent">Contrasena actual</label><input id="pwdCurrent" type="password" autocomplete="current-password"></div>
    <div class="field"><label for="pwdNew">Nueva contrasena</label><input id="pwdNew" type="password" autocomplete="new-password"></div>
    <div class="field"><label for="pwdConfirm">Confirmar nueva contrasena</label><input id="pwdConfirm" type="password" autocomplete="new-password"></div>
    <div class="login-note">Minimo 8 caracteres con mayuscula, minuscula, numero y simbolo.</div>
    <div id="pwdMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="pwdSave" class="btn primary" type="button">Actualizar contrasena</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("pwdSave").onclick = submitSelfPassword;
  $("pwdCurrent").focus();
}

function showPwdMsg(text, ok) {
  const box = $("pwdMsg");
  box.classList.remove("hidden");
  box.textContent = text;
  box.style.borderColor = ok ? "rgba(52,211,153,.45)" : "rgba(248,113,113,.45)";
}

async function submitSelfPassword() {
  const current = $("pwdCurrent").value;
  const next = $("pwdNew").value;
  const confirm = $("pwdConfirm").value;
  const problem = passwordPolicyMessage(next);
  if (problem) { showPwdMsg(problem, false); return; }
  if (next !== confirm) { showPwdMsg("La confirmacion no coincide", false); return; }
  $("pwdSave").disabled = true;
  try {
    const data = await api("/api/v1/auth/password", {
      method:"PUT", body:JSON.stringify({ current_password: current, new_password: next }),
    });
    showPwdMsg("Contrasena actualizada. Correo de confirmacion: " + (data.email_sent ? "SENT" : "FAILED"), true);
    toast(data.email_sent ? "Contrasena actualizada y correo enviado" : "Contrasena actualizada; el correo fallo", data.email_sent ? "ok" : "bad");
    if (data.email_sent) setTimeout(closeModal, 1200);
  } catch (err) {
    showPwdMsg("Error: " + err.message, false);
  } finally {
    $("pwdSave").disabled = false;
  }
}

function openAdminResetModal(userId, email) {
  $("modalTitle").textContent = "Resetear contrasena";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="result-box">Usuario: <b>${esc(email)}</b><br>Se enviara la nueva contrasena por correo y quedara auditada.</div>
    <label class="check-row"><input id="resetRandom" type="checkbox"><span>Generar contrasena aleatoria</span></label>
    <div class="field"><label for="resetNewPassword">Nueva contrasena temporal</label><input id="resetNewPassword" type="password" autocomplete="new-password" placeholder="Min. 8, mayuscula, minuscula, numero y simbolo"></div>
    <div class="login-note">Si marcas generar aleatoria, el administrador no vera la contrasena; solo se envia al usuario.</div>
    <div id="resetMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="resetSave" class="btn primary" type="button">Resetear y notificar</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("resetRandom").onchange = () => {
    $("resetNewPassword").disabled = $("resetRandom").checked;
    if ($("resetRandom").checked) $("resetNewPassword").value = "";
  };
  $("resetSave").onclick = () => submitAdminReset(userId);
  $("resetNewPassword").focus();
}

function showResetMsg(text, ok) {
  const box = $("resetMsg");
  box.classList.remove("hidden");
  box.textContent = text;
  box.style.borderColor = ok ? "rgba(52,211,153,.45)" : "rgba(248,113,113,.45)";
}

async function submitAdminReset(userId) {
  const generate = $("resetRandom").checked;
  const value = $("resetNewPassword").value;
  const body = { generate_random: generate };
  if (!generate) {
    const problem = passwordPolicyMessage(value);
    if (problem) { showResetMsg(problem, false); return; }
    body.new_password = value;
  }
  $("resetSave").disabled = true;
  try {
    const data = await api(`/api/v1/admin/users/${userId}/reset-password`, {
      method:"PUT", body:JSON.stringify(body),
    });
    const mode = data.generated ? "aleatoria" : "manual";
    showResetMsg(`Contrasena ${mode} actualizada. Correo: ${data.email_sent ? "SENT" : "FAILED"}.`, data.email_sent);
    toast(data.email_sent ? `Contrasena ${mode} actualizada y enviada` : "Contrasena actualizada; el correo fallo", data.email_sent ? "ok" : "bad");
    await loadEmailLogs();
    if (data.email_sent) setTimeout(closeModal, 1200);
  } catch (err) {
    showResetMsg("Error: " + err.message, false);
  } finally {
    $("resetSave").disabled = false;
  }
}

async function loadSkills() {
  try {
    const data = await api("/api/v1/skills");
    state.skills = data.skills || [];
    state.skillViewer = data.viewer_capabilities || {};
    const canCreate = !!data.can_create || state.role === "ADMIN";
    $("newSkillBtn").classList.toggle("hidden", !canCreate);
    renderSkills();
  } catch (err) {
    toast(err.message, "bad");
  }
}

function renderSkills() {
  const grid = $("skillsGrid");
  if (!grid) return;
  const q = ($("skillSearch").value || "").toLowerCase();
  const category = $("skillCategoryFilter").value;
  const skills = (state.skills || []).filter(s => {
    const hay = `${s.display_name} ${s.slug} ${s.trigger_description} ${s.category}`.toLowerCase();
    return (category === "all" || s.category === category) && (!q || hay.includes(q));
  });
  $("skillsCount").textContent = `${skills.length} skill(s)`;
  if (!skills.length) {
    grid.innerHTML = '<div class="card"><div class="muted">No hay skills para este filtro. Crea una con “+ Nueva Skill” o revisa el área seleccionada.</div></div>';
    return;
  }
  grid.innerHTML = skills.map(s => `<article class="card project-card">
    <div class="row">
      <div style="flex:1;min-width:0"><h2>${esc(s.display_name)}</h2><div class="muted mono" style="font-size:12px">${esc(s.slug)}</div></div>
      ${s.is_official ? '<span class="pill info">OFICIAL</span>' : '<span class="pill">PRIVADA</span>'}
      ${s.is_read_only ? '<span class="pill warn">SOLO LECTURA</span>' : ""}
    </div>
    <div class="meta"><span class="pill">${esc(SKILL_CATEGORY_LABELS[s.category] || s.category)}</span><span class="pill">${esc(s.compiled_tokens)} tokens</span><span class="pill">v${esc(s.version)}</span></div>
    <div class="muted" style="font-size:12.5px">${esc(s.trigger_description)}</div>
    <div class="row" style="margin-top:auto">
      <button class="btn sm" data-view-skill="${esc(s.slug)}" type="button">Ver manual</button>
      ${s.can_edit ? `<button class="btn sm" data-edit-skill="${esc(s.slug)}" type="button">Editar</button>` : ""}
      ${s.can_clone ? `<button class="btn sm" data-clone-skill="${esc(s.slug)}" type="button">Clonar</button>` : ""}
      ${s.can_grant ? `<button class="btn sm" data-grant-skill="${esc(s.slug)}" type="button">Permisos</button>` : ""}
    </div>
  </article>`).join("");
  grid.querySelectorAll("[data-view-skill]").forEach(btn => { btn.onclick = () => openSkillManual(btn.dataset.viewSkill); });
  grid.querySelectorAll("[data-edit-skill]").forEach(btn => { btn.onclick = () => openSkillEditor(btn.dataset.editSkill); });
  grid.querySelectorAll("[data-clone-skill]").forEach(btn => { btn.onclick = () => cloneSkill(btn.dataset.cloneSkill); });
  grid.querySelectorAll("[data-grant-skill]").forEach(btn => { btn.onclick = () => openSkillAclModal(btn.dataset.grantSkill); });
}

async function openSkillManual(slug) {
  try {
    const skill = await api(`/api/v1/skills/${slug}`);
    $("modalTitle").textContent = `Manual · ${skill.display_name}`;
    $("modalBody").innerHTML = `<div class="stack" style="gap:10px">
      <div class="row"><span class="pill info">${esc(SKILL_CATEGORY_LABELS[skill.category] || skill.category)}</span>${skill.is_official ? '<span class="pill info">Oficial</span>' : ""}<span class="pill">${esc(skill.compiled_tokens)} tokens</span></div>
      <div class="muted" style="font-size:12px">${esc(skill.trigger_description)}</div>
      <pre class="result-box" style="white-space:pre-wrap;max-height:52dvh;overflow:auto">${esc(skill.compiled_skill_md)}</pre>
    </div>`;
    $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cerrar</button><button id="copySkillBtn" class="btn primary" type="button">Copiar SKILL.md</button>`;
    $("modal").classList.remove("hidden");
    $("modalCancel").onclick = closeModal;
    $("copySkillBtn").onclick = () => copyText(skill.compiled_skill_md, "SKILL.md");
  } catch (err) { toast(err.message, "bad"); }
}

async function cloneSkill(slug) {
  if (!confirm(`¿Clonar ${slug} como una skill privada editable?`)) return;
  try {
    const data = await api(`/api/v1/skills/${slug}/clone`, { method:"POST" });
    toast(`Skill clonada: ${data.slug}`, "ok");
    await loadSkills();
  } catch (err) { toast(err.message, "bad"); }
}

function skillEditorFields() {
  return {
    display_name: $("skName").value.trim(),
    slug: $("skSlug").value.trim() || null,
    category: $("skCategory").value,
    trigger_description: $("skTrigger").value.trim(),
    objective: $("skObjective").value.trim(),
    workflow_steps: $("skSteps").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    guardrails_forbidden: $("skGuardrails").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    output_criteria: $("skOutput").value.trim(),
    is_official: $("skOfficial") ? $("skOfficial").checked : false,
    visibility: $("skVisibility") ? $("skVisibility").value : "PRIVATE",
    status: "PUBLISHED",
  };
}

function showSkillMsg(text, ok) {
  const box = $("skMsg");
  box.classList.remove("hidden");
  box.textContent = text;
  box.style.borderColor = ok ? "rgba(52,211,153,.45)" : "rgba(248,113,113,.45)";
}

function scheduleSkillPreview() {
  if (state.skillPreviewTimer) clearTimeout(state.skillPreviewTimer);
  state.skillPreviewTimer = setTimeout(updateSkillPreview, 450);
}

async function updateSkillPreview() {
  const preview = $("skPreview");
  if (!preview) return;
  const body = skillEditorFields();
  try {
    const data = await api("/api/v1/skills/preview", { method:"POST", body:JSON.stringify(body) });
    preview.textContent = data.compiled_skill_md;
    $("skTokens").textContent = `${data.compiled_tokens} tokens estimados`;
  } catch (err) {
    preview.textContent = "Completa los campos para ver la vista previa.\n\n" + err.message;
    $("skTokens").textContent = "";
  }
}

function renderCategoryOptions(selected) {
  return Object.entries(SKILL_CATEGORY_LABELS).map(([value, label]) =>
    `<option value="${value}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`
  ).join("");
}

async function openSkillEditor(slug) {
  let skill = null;
  if (slug) {
    try { skill = await api(`/api/v1/skills/${slug}`); }
    catch (err) { toast(err.message, "bad"); return; }
  }
  const isAdmin = state.role === "ADMIN";
  state.editingSkillSlug = slug || null;
  $("modalTitle").textContent = slug ? `Editar · ${skill.display_name}` : "Nueva Skill";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="result-box">El servidor compila el estándar SKILL.md por ti. Tú solo describes la habilidad en lenguaje natural.</div>
    <div class="form-grid">
      <div class="field"><label for="skName">Nombre de la habilidad</label><input id="skName" value="${esc(skill ? skill.display_name : "")}" placeholder="Ej. Resumen de actas"></div>
      <div class="field"><label for="skSlug">Slug (opcional)</label><input id="skSlug" value="${esc(skill ? skill.slug : "")}" ${slug ? "disabled" : ""} placeholder="resumen-actas"></div>
      <div class="field"><label for="skCategory">Área</label><select id="skCategory">${renderCategoryOptions(skill ? skill.category : "general")}</select></div>
      <div class="field"><label for="skTrigger">¿Cuándo debe usarla?</label><input id="skTrigger" value="${esc(skill ? skill.trigger_description : "")}" placeholder="Cuando la tarea requiera..."></div>
    </div>
    <div class="field"><label for="skObjective">Objetivo (una frase)</label><textarea id="skObjective" rows="2" placeholder="Qué logra esta habilidad">${esc(skill ? skill.objective : "")}</textarea></div>
    <div class="field"><label for="skSteps">Pasos obligatorios (uno por línea, 3 a 5)</label><textarea id="skSteps" rows="5" placeholder="1. Lee el documento&#10;2. Extrae...">${esc(skill ? (skill.workflow_steps || []).join("\n") : "")}</textarea></div>
    <div class="field"><label for="skGuardrails">Prohibiciones (una por línea)</label><textarea id="skGuardrails" rows="3" placeholder="No inventar fechas&#10;No omitir la fuente">${esc(skill ? (skill.guardrails_forbidden || []).join("\n") : "")}</textarea></div>
    <div class="field"><label for="skOutput">¿Cómo entrega el resultado?</label><textarea id="skOutput" rows="2" placeholder="Tabla Markdown con campo, valor y fuente">${esc(skill ? skill.output_criteria : "")}</textarea></div>
    ${isAdmin ? `<div class="row"><label class="check-row" style="flex:1"><input id="skOfficial" type="checkbox" ${skill && skill.is_official ? "checked" : ""}><span>Skill oficial (solo Admin edita)</span></label><select id="skVisibility" style="max-width:180px"><option value="PRIVATE" ${skill && skill.visibility === "PRIVATE" ? "selected" : ""}>Privada</option><option value="SHARED" ${skill && skill.visibility === "SHARED" ? "selected" : ""}>Compartida</option></select></div>` : ""}
    <div class="card" style="background:#08111f">
      <div class="row"><label class="muted" style="font-size:12px">Autocompletar desde una idea simple</label></div>
      <div class="row"><input id="skIdea" class="grow" placeholder="Quiero que el agente revise facturas y saque el total sin inventar datos"><button id="skDraftBtn" class="btn sm" type="button">Generar borrador</button></div>
    </div>
    <div class="row"><span class="muted" style="font-size:12px">Vista previa del estándar</span><span id="skTokens" class="pill"></span></div>
    <pre id="skPreview" class="result-box" style="white-space:pre-wrap;max-height:36dvh;overflow:auto"></pre>
    <div id="skMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="skPreviewBtn" class="btn" type="button">Actualizar vista previa</button><button id="skSaveBtn" class="btn primary" type="button">${slug ? "Guardar cambios" : "Guardar Skill"}</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("skPreviewBtn").onclick = updateSkillPreview;
  $("skSaveBtn").onclick = submitSkill;
  $("skDraftBtn").onclick = draftSkillFromIdea;
  ["skName", "skTrigger", "skObjective", "skSteps", "skGuardrails", "skOutput"].forEach(id => {
    const el = $(id); if (el) el.oninput = scheduleSkillPreview;
  });
  updateSkillPreview();
}

async function draftSkillFromIdea() {
  const raw = $("skIdea").value.trim();
  if (raw.length < 10) { showSkillMsg("Escribe una idea un poco más descriptiva", false); return; }
  $("skDraftBtn").disabled = true;
  try {
    const data = await api("/api/v1/skills/draft-from-idea", {
      method:"POST", body:JSON.stringify({ raw_idea: raw, category: $("skCategory").value }),
    });
    $("skName").value = data.display_name;
    if (!$("skSlug").disabled) $("skSlug").value = data.slug;
    $("skCategory").value = data.category;
    $("skTrigger").value = data.trigger_description;
    $("skObjective").value = data.objective;
    $("skSteps").value = (data.workflow_steps || []).join("\n");
    $("skGuardrails").value = (data.guardrails_forbidden || []).join("\n");
    $("skOutput").value = data.output_criteria;
    $("skPreview").textContent = data.compiled_skill_md;
    $("skTokens").textContent = `${data.compiled_tokens} tokens · ${data.provider}`;
    showSkillMsg("Borrador listo. Revísalo y guarda.", true);
  } catch (err) {
    showSkillMsg("Error: " + err.message, false);
  } finally {
    $("skDraftBtn").disabled = false;
  }
}

async function submitSkill() {
  const body = skillEditorFields();
  $("skSaveBtn").disabled = true;
  try {
    const data = state.editingSkillSlug
      ? await api(`/api/v1/skills/${state.editingSkillSlug}`, { method:"PUT", body:JSON.stringify(body) })
      : await api("/api/v1/skills", { method:"POST", body:JSON.stringify(body) });
    toast(`Skill ${data.slug} guardada`, "ok");
    await loadSkills();
    closeModal();
  } catch (err) {
    showSkillMsg("Error: " + err.message, false);
  } finally {
    $("skSaveBtn").disabled = false;
  }
}

async function openUserCapabilitiesModal(userId, email) {
  const user = (state.adminUsers || []).find(u => u.id === userId);
  const caps = (user && user.skill_capabilities) || {};
  $("modalTitle").textContent = "Permisos de skills";
  $("modalBody").innerHTML = `<div class="stack" style="gap:12px">
    <div class="result-box">Usuario: <b>${esc(email)}</b>${user && user.role === "ADMIN" ? "<br>Los ADMIN tienen bypass total; estos flags aplican a ingenieros." : ""}</div>
    <label class="check-row"><input id="capCreate" type="checkbox" ${caps.can_create_skills ? "checked" : ""}><span>Puede crear skills privadas</span></label>
    <label class="check-row"><input id="capClone" type="checkbox" ${caps.can_clone_skills ? "checked" : ""}><span>Puede clonar skills oficiales o compartidas</span></label>
    <label class="check-row"><input id="capEditOwn" type="checkbox" ${caps.can_edit_own_skills ? "checked" : ""}><span>Puede editar sus propias skills</span></label>
    <label class="check-row"><input id="capGrant" type="checkbox" ${caps.can_grant_skills ? "checked" : ""}><span>Puede otorgar/permisos sobre sus skills</span></label>
    <div class="login-note">Las skills oficiales siempre son de solo lectura para ingenieros; solo el Admin puede editarlas.</div>
    <div id="capMsg" class="result-box hidden"></div>
  </div>`;
  $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cancelar</button><button id="capSave" class="btn primary" type="button">Guardar permisos</button>`;
  $("modal").classList.remove("hidden");
  $("modalCancel").onclick = closeModal;
  $("capSave").onclick = async () => {
    $("capSave").disabled = true;
    try {
      await api(`/api/v1/admin/users/${userId}/skill-capabilities`, {
        method:"PUT",
        body:JSON.stringify({
          can_create_skills: $("capCreate").checked,
          can_clone_skills: $("capClone").checked,
          can_edit_own_skills: $("capEditOwn").checked,
          can_grant_skills: $("capGrant").checked,
        }),
      });
      toast("Permisos de skills actualizados", "ok");
      await loadAdminUsers();
      closeModal();
    } catch (err) {
      const box = $("capMsg");
      box.classList.remove("hidden");
      box.textContent = "Error: " + err.message;
      box.style.borderColor = "rgba(248,113,113,.45)";
    } finally {
      $("capSave").disabled = false;
    }
  };
}

async function openSkillAclModal(slug) {
  try {
    if (!state.adminUsers.length) await loadAdminUsers();
    const [detail, perms] = await Promise.all([
      api(`/api/v1/skills/${slug}`),
      api(`/api/v1/skills/${slug}/permissions`),
    ]);
    const users = state.adminUsers || [];
    const assigned = new Map((perms.permissions || []).map(p => [p.user_id, p]));
    $("modalTitle").textContent = `Permisos · ${detail.display_name}`;
    $("modalBody").innerHTML = `<div class="stack" style="gap:8px">
      <div class="result-box">Marca quién puede usar, editar, clonar u otorgar esta skill. Solo el Admin o el dueño con permiso de otorgar puede administrar esto.</div>
      <div id="aclList" class="stack" style="gap:6px;max-height:50dvh;overflow:auto">
        ${users.map(u => {
          const p = assigned.get(u.id) || {can_use:false, can_edit:false, can_clone:false, can_grant:false};
          return `<div class="check-row" data-acl-user="${esc(u.id)}"><div class="grow"><b>${esc(u.email)}</b><div class="muted" style="font-size:11px">${esc(u.full_name || "")}</div></div><div class="row" style="gap:5px"><label class="muted" style="font-size:10px"><input type="checkbox" class="acl-use" ${p.can_use ? "checked" : ""}> usar</label><label class="muted" style="font-size:10px"><input type="checkbox" class="acl-edit" ${p.can_edit ? "checked" : ""}> editar</label><label class="muted" style="font-size:10px"><input type="checkbox" class="acl-clone" ${p.can_clone ? "checked" : ""}> clonar</label><label class="muted" style="font-size:10px"><input type="checkbox" class="acl-grant" ${p.can_grant ? "checked" : ""}> otorgar</label></div></div>`;
        }).join("")}
      </div>
      <div id="aclMsg" class="result-box hidden"></div>
    </div>`;
    $("modalActions").innerHTML = `<button id="modalCancel" class="btn ghost" type="button">Cerrar</button><button id="aclSave" class="btn primary" type="button">Guardar permisos</button>`;
    $("modal").classList.remove("hidden");
    $("modalCancel").onclick = closeModal;
    $("aclSave").onclick = async () => {
      $("aclSave").disabled = true;
      try {
        for (const row of document.querySelectorAll("[data-acl-user]")) {
          await api(`/api/v1/skills/${slug}/permissions`, {
            method:"PUT",
            body:JSON.stringify({
              user_id: row.dataset.aclUser,
              can_use: row.querySelector(".acl-use").checked,
              can_edit: row.querySelector(".acl-edit").checked,
              can_clone: row.querySelector(".acl-clone").checked,
              can_grant: row.querySelector(".acl-grant").checked,
            }),
          });
        }
        toast("Permisos de la skill actualizados", "ok");
        closeModal();
      } catch (err) { toast(err.message, "bad"); }
      finally { $("aclSave").disabled = false; }
    };
  } catch (err) { toast(err.message, "bad"); }
}

async function loadKnowledge() {
  if (!state.projects.length) {
    try { await refreshProjects(); } catch (err) { toast(err.message, "bad"); }
  }
  const select = $("kbProject");
  const prev = select.value;
  select.innerHTML = state.projects.map(p => `<option value="${esc(p.id)}">${esc(p.display_name)} · ${esc(p.project_slug)}</option>`).join("");
  if (prev && [...select.options].some(o => o.value === prev)) select.value = prev;
  $("kbBootstrap").classList.toggle("hidden", state.role !== "ADMIN");
  if (!select.value) {
    $("kbStatus").textContent = "No hay proyectos disponibles.";
    $("kbDocs").innerHTML = '<tr><td colspan="5" class="muted">Crea o solicita acceso a un proyecto.</td></tr>';
    return;
  }
  await loadKnowledgeDocs();
}

async function loadKnowledgeDocs() {
  const projectId = $("kbProject").value;
  if (!projectId) return;
  $("kbStatus").textContent = "Cargando documentos…";
  try {
    const data = await api(`/api/v1/projects/${projectId}/documents`);
    const docs = data.documents || [];
    const project = state.projects.find(p => p.id === projectId);
    const worker = project?.hermes_worker;
    const workerText = worker ? ` · Hermes ${worker.status}` : " · Hermes sin worker";
    $("kbStatus").textContent = `${docs.length} documento(s) indexado(s)${workerText}`;
    $("kbDocs").innerHTML = docs.length ? docs.map(d => `<tr>
      <td class="mono" style="font-size:11px">${esc(d.source_uri)}</td>
      <td><span class="pill">${esc(d.doc_type)}</span></td>
      <td>${esc(d.title)}</td>
      <td>${esc(d.chunks)}</td>
      <td class="muted" style="font-size:11px">${esc(d.updated_at ? new Date(d.updated_at).toLocaleString() : "—")}</td>
    </tr>`).join("") : '<tr><td colspan="5" class="muted">Sin documentos. Un Admin puede ejecutar docs-curator para el bootstrap.</td></tr>';
  } catch (err) {
    $("kbStatus").textContent = "Error al cargar documentos";
    toast(err.message, "bad");
  }
}

async function searchKnowledge() {
  const projectId = $("kbProject").value;
  const query = $("kbSearch").value.trim();
  if (!projectId || !query) { toast("Selecciona un proyecto y escribe una búsqueda", "bad"); return; }
  $("kbSearchBtn").disabled = true;
  try {
    const data = await api(`/api/v1/projects/${projectId}/documents/search`, {
      method:"POST", body:JSON.stringify({ query, limit: 8 }),
    });
    const chunks = data.chunks || [];
    $("kbResults").innerHTML = chunks.length ? chunks.map(c => `<div class="card" style="padding:12px">
      <div class="row"><span class="pill info">${esc(c.doc_type)}</span><span class="mono" style="font-size:11px">${esc(c.source_uri)}</span><span class="pill">${esc(c.similarity)} similitud</span></div>
      <div style="font-weight:650;margin:4px 0">${esc(c.title)}${c.section_heading ? " · " + esc(c.section_heading) : ""}</div>
      <div class="muted" style="white-space:pre-wrap;font-size:12px;max-height:160px;overflow:auto">${esc(c.chunk_text)}</div>
    </div>`).join("") : '<div class="result-box">Sin coincidencias documentales para esa búsqueda.</div>';
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    $("kbSearchBtn").disabled = false;
  }
}

async function bootstrapKnowledge() {
  const projectId = $("kbProject").value;
  if (!projectId) { toast("Selecciona un proyecto", "bad"); return; }
  if (!confirm("¿Ejecutar el worker docs-curator para escanear e indexar la documentación del proyecto?")) return;
  $("kbBootstrap").disabled = true;
  $("kbStatus").textContent = "Ejecutando docs-curator… esto puede tardar";
  try {
    const data = await api(`/api/v1/projects/${projectId}/documents/bootstrap`, { method:"POST" });
    $("kbStatus").textContent = `Bootstrap completado · ${data.documents_count || 0} documento(s) · errores: ${(data.errors || []).length}`;
    toast("Documentación indexada por docs-curator", "ok");
    await loadKnowledgeDocs();
    if (state.role === "ADMIN") refreshTopology().catch(() => {});
  } catch (err) {
    $("kbStatus").textContent = "Error en bootstrap documental";
    toast(err.message, "bad");
  } finally {
    $("kbBootstrap").disabled = false;
  }
}

async function loadLicense() {
  try {
    const data = await api("/api/v1/admin/license");
    state.license = data;
    const plan = $("licensePlan");
    const signed = data.signed_license || {};
    const allowed = signed.allowed_plans || [];
    const plans = Object.keys(data.plans || {}).filter(code => !allowed.length || allowed.includes(code));
    plan.innerHTML = plans.map(code => `<option value="${esc(code)}">${esc(code)}</option>`).join("");
    if (plans.includes(data.plan_code)) plan.value = data.plan_code;
    const limits = data.limits || {};
    const ceilings = signed.signed_limits || {};
    const fields = {
      licenseActiveProjects: "max_active_projects",
      licenseHermesSessions: "max_hermes_sessions",
      licenseEngineersProject: "max_engineers_per_project",
      licenseEngineersTotal: "max_total_engineer_sessions",
      licenseUsers: "max_registered_users",
      licenseParallelJobs: "max_parallel_rag_jobs",
    };
    Object.entries(fields).forEach(([id, field]) => {
      const input = $(id);
      input.value = limits[field] ?? ceilings[field] ?? 0;
      if (ceilings[field] != null) input.max = ceilings[field];
    });
    renderLicense(data);
  } catch (err) {
    toast(err.message, "bad");
    $("licenseStatus").textContent = "Error al cargar licencia: " + err.message;
  }
}

function renderLicense(data) {
  const usage = data.usage || {};
  const jobs = usage.hermes_jobs || {};
  const signed = data.signed_license || {};
  const status = signed.status || "UNKNOWN";
  const statusClass = status === "VALID" ? "ok" : status === "GRACE" ? "warn" : "bad";
  $("licenseSignedStatus").textContent = status;
  $("licenseSignedStatus").className = `pill ${statusClass}`;
  $("licenseSignedCustomer").textContent = signed.customer || "Sin cliente firmado";
  $("licenseSignedMeta").textContent = [
    signed.license_id ? `Licencia ${signed.license_id}` : "Sin license_id",
    signed.installation_id ? `Instalación ${signed.installation_id}` : "Sin installation_id",
    signed.expires_at ? `Expira ${new Date(signed.expires_at).toLocaleString()}` : "Sin expiración",
    signed.grace_until ? `Gracia hasta ${new Date(signed.grace_until).toLocaleString()}` : "Sin gracia",
    signed.error_message || "",
  ].filter(Boolean).join(" · ");
  const act = data.activation || {};
  const actStatus = act.status || "UNKNOWN";
  const actClass = ["ACTIVE", "OFFLINE_GRACE", "TEST_BYPASS", "NOT_CONFIGURED"].includes(actStatus) ? (actStatus === "ACTIVE" || actStatus === "TEST_BYPASS" ? "ok" : "warn") : "bad";
  $("licenseActivationStatus").textContent = `Activación ${actStatus}`;
  $("licenseActivationStatus").className = `pill ${actClass}`;
  $("licenseActivationMeta").textContent = [
    act.server_url ? `Servidor ${act.server_url}` : "Sin servidor de activación",
    act.lease_expires_at ? `Lease hasta ${new Date(act.lease_expires_at).toLocaleString()}` : "Sin lease",
    act.lease_grace_until ? `Gracia offline hasta ${new Date(act.lease_grace_until).toLocaleString()}` : "",
    act.last_success_at ? `Última activación ${new Date(act.last_success_at).toLocaleString()}` : "",
    act.last_error || "",
  ].filter(Boolean).join(" · ");
  const operational = Boolean(data.license_operational && data.activation_operational);
  const canMutate = state.role === "LICENSE_ADMIN";
  const governance = $("licenseGovernanceNote");
  if (governance) {
    governance.textContent = canMutate
      ? "Operador de licencia con MFA. Cada cambio de plan, cuota o activación queda auditado con actor e IP."
      : "Modo lectura. Solo la cuenta LICENSE_ADMIN con MFA puede cambiar el plan, las cuotas o la activación.";
    governance.className = `card ${canMutate ? "ok" : ""}`;
    governance.style.padding = "12px 14px";
    governance.style.fontSize = "13px";
  }
  ["licensePlan", "licenseActiveProjects", "licenseHermesSessions", "licenseEngineersProject",
   "licenseEngineersTotal", "licenseUsers", "licenseParallelJobs"].forEach(id => {
    const element = $(id);
    if (element) element.disabled = !operational || !canMutate;
  });
  $("licenseApply").disabled = !operational || !canMutate;
  $("licenseActivate").disabled = !data.license_operational || !canMutate;
  const audit = data.audit_chain || {};
  $("licenseAuditState").textContent = audit.valid ? `Audit OK (${audit.events_checked})` : `Audit roto en #${audit.broken_at}`;
  $("licenseAuditState").className = `pill ${audit.valid ? "ok" : "bad"}`;
  $("licenseOverLimit").classList.toggle("hidden", operational && !data.over_limit);
  $("licenseOverLimit").textContent = !data.license_operational ? `Licencia ${status}` : (!data.activation_operational ? `Activación ${actStatus}` : "Over limit");
  $("licenseStatus").textContent = `Plan ${data.plan_code} · ${status} · ${actStatus} · actualizado ${data.updated_at ? new Date(data.updated_at).toLocaleString() : "—"}${data.over_limit ? " · uso por encima del plan" : ""}`;
  const cards = [
    ["Proyectos activos", usage.active_projects_used, data.limits?.max_active_projects],
    ["Hermes online", usage.hermes_workers_online, data.limits?.max_hermes_sessions],
    ["Ingenieros online", usage.total_engineers_online, data.limits?.max_total_engineer_sessions],
    ["Sesiones de ingeniero", usage.total_engineer_sessions_online, data.limits?.max_total_engineer_sessions],
    ["Usuarios registrados", usage.registered_engineers, data.limits?.max_registered_users],
    ["Jobs RAG", `${jobs.queued || 0}Q / ${jobs.running || 0}R / ${jobs.completed || 0}C / ${jobs.failed || 0}F`, "—"],
  ];
  $("licenseUsage").innerHTML = cards.map(([label, used, limit]) => `<div class="card" style="padding:14px">
    <div class="muted" style="font-size:12px">${esc(label)}</div>
    <div style="font-size:26px;font-weight:800;margin-top:4px">${esc(String(used ?? 0))}${limit !== "—" && limit != null ? `<span class="muted" style="font-size:13px"> / ${esc(String(limit))}</span>` : ""}</div>
  </div>`).join("");
  const recent = data.recent_jobs || [];
  $("licenseJobs").innerHTML = recent.length ? recent.map(j => `<tr>
    <td class="mono" style="font-size:11px">${esc(j.project_slug || j.project_id)}</td>
    <td>${esc(j.job_type)}</td>
    <td><span class="pill ${j.status === "FAILED" ? "bad" : j.status === "COMPLETED" ? "ok" : j.status === "RUNNING" ? "warn" : ""}">${esc(j.status)}</span></td>
    <td>${esc(j.attempts)}</td>
    <td class="muted" style="font-size:11px">${esc(j.created_at ? new Date(j.created_at).toLocaleString() : "—")}</td>
    <td class="muted" style="font-size:11px">${esc(j.error_message || "—")}</td>
  </tr>`).join("") : '<tr><td colspan="6" class="muted">Sin jobs registrados.</td></tr>';
  const auditRows = data.recent_audit || [];
  $("licenseAuditRows").innerHTML = auditRows.length ? auditRows.map(e => `<tr>
    <td class="mono">${esc(e.id)}</td>
    <td>${esc(e.event_type)}</td>
    <td>${esc(e.actor || "—")}</td>
    <td class="mono" style="font-size:10px">${esc(String(e.event_hash || "").slice(0, 12))}…</td>
    <td class="muted" style="font-size:11px">${esc(e.created_at ? new Date(e.created_at).toLocaleString() : "—")}</td>
  </tr>`).join("") : '<tr><td colspan="5" class="muted">Sin eventos de auditoría.</td></tr>';
}

async function applyLicense() {
  const body = {
    plan_code: $("licensePlan").value,
    max_active_projects: Number($("licenseActiveProjects").value),
    max_hermes_sessions: Number($("licenseHermesSessions").value),
    max_engineers_per_project: Number($("licenseEngineersProject").value),
    max_total_engineer_sessions: Number($("licenseEngineersTotal").value),
    max_registered_users: Number($("licenseUsers").value),
    max_parallel_rag_jobs: Number($("licenseParallelJobs").value),
  };
  $("licenseApply").disabled = true;
  try {
    const data = await api("/api/v1/admin/license", { method:"PUT", body:JSON.stringify(body) });
    renderLicense(data);
    toast("Licencia actualizada", "ok");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    $("licenseApply").disabled = state.role !== "LICENSE_ADMIN";
  }
}

async function activateLicense() {
  $("licenseActivate").disabled = true;
  try {
    const data = await api("/api/v1/admin/license/activate", { method:"POST" });
    if (data.license) renderLicense(data.license);
    toast("Activación renovada", "ok");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    $("licenseActivate").disabled = state.role !== "LICENSE_ADMIN";
  }
}

async function toggleProjectSlot(projectId, active) {
  const verb = active ? "activar" : "desactivar";
  if (!confirm(`¿${verb[0].toUpperCase() + verb.slice(1)} el slot de este proyecto?`)) return;
  try {
    await api(`/api/v1/projects/${projectId}/toggle-slot`, {
      method:"POST", body:JSON.stringify({ active }),
    });
    toast(`Slot ${active ? "activado" : "desactivado"}`, "ok");
    await refreshProjects();
    if (state.activeView === "license") await loadLicense();
  } catch (err) {
    toast(err.message, "bad");
  }
}

function bindStaticEvents() {
  $("loginForm").addEventListener("submit", doEmailLogin);
  $("loginTabEmail").onclick = () => setLoginTab("email");
  $("loginTabKey").onclick = () => setLoginTab("key");
  $("adminKeyBtn").onclick = doKeyLogin;
  $("logoutBtn").onclick = logout;
  $("passwordBtn").onclick = openSelfPasswordModal;
  $("menuBtn").onclick = openDrawer;
  $("drawerBackdrop").onclick = closeDrawer;
  $("modalClose").onclick = closeModal;
  $("newProjectBtn").onclick = openProjectModal;
  $("kbRefresh").onclick = () => loadKnowledgeDocs();
  $("kbProject").onchange = () => loadKnowledgeDocs();
  $("kbSearchBtn").onclick = searchKnowledge;
  $("kbBootstrap").onclick = bootstrapKnowledge;
  $("kbSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") searchKnowledge(); });
  $("licenseRefresh").onclick = () => loadLicense();
  $("licenseApply").onclick = applyLicense;
  $("licenseActivate").onclick = activateLicense;
  $("licensePlan").onchange = () => {
    const limits = state.license?.plans?.[$("licensePlan").value];
    if (!limits) return;
    const ceilings = state.license?.signed_license?.signed_limits || {};
    const clamp = (field) => Math.min(limits[field], ceilings[field] ?? limits[field]);
    $("licenseActiveProjects").value = clamp("max_active_projects");
    $("licenseHermesSessions").value = clamp("max_hermes_sessions");
    $("licenseEngineersProject").value = clamp("max_engineers_per_project");
    $("licenseEngineersTotal").value = clamp("max_total_engineer_sessions");
    $("licenseUsers").value = clamp("max_registered_users");
    $("licenseParallelJobs").value = clamp("max_parallel_rag_jobs");
  };
  $("newSkillBtn").onclick = () => openSkillEditor(null);
  $("skillsRefresh").onclick = () => loadSkills();
  $("skillSearch").oninput = renderSkills;
  $("skillCategoryFilter").onchange = renderSkills;
  $("projectSearch").oninput = renderProjects;
  $("newUserBtn").onclick = openCreateUserModal;
  $("usersRefresh").onclick = () => loadAdminUsers();
  $("userSearch").oninput = renderAdminUsers;
  $("myRefresh").onclick = () => refreshProjects().catch(err => toast(err.message, "bad"));
  $("smtpSave").onclick = () => saveSmtp(false);
  $("smtpSaveTest").onclick = () => saveSmtp(true);
  $("smtpTest").onclick = sendSmtpTest;
  $("smtpLogsRefresh").onclick = loadEmailLogs;
  $("smtpTls").onchange = () => { if ($("smtpTls").checked) $("smtpSsl").checked = false; };
  $("smtpSsl").onchange = () => { if ($("smtpSsl").checked) $("smtpTls").checked = false; };
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
