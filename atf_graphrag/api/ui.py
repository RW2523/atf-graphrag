"""Single-page application served at GET / — professional end-to-end UI:
upload (files/folders) → chat with cited answers → knowledge-graph exploration.
Talks to the stdlib JSON API (/api/upload, /query, /stats, /graph/export,
/api/chunk, /api/communities/build). Self-contained (D3 v7 from CDN for the graph)."""

INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ATF GraphRAG — Console</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
:root{
  --bg:#0f172a; --bg2:#1e293b; --panel:#ffffff; --ink:#0f172a; --muted:#64748b;
  --line:#e2e8f0; --accent:#4f46e5; --accent2:#6366f1; --accent-soft:#eef2ff;
  --ok:#16a34a; --warn:#d97706; --err:#dc2626; --chip:#f1f5f9;
  --shadow:0 1px 3px rgba(15,23,42,.08),0 8px 24px rgba(15,23,42,.06);
}
*{box-sizing:border-box} html,body{height:100%}
body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  color:var(--ink);background:#f8fafc;display:flex;height:100vh;overflow:hidden}
.side{width:240px;background:linear-gradient(180deg,#0f172a,#1e293b);color:#cbd5e1;
  display:flex;flex-direction:column;flex-shrink:0}
.brand{display:flex;align-items:center;gap:10px;padding:20px 18px;color:#fff;
  font-weight:700;font-size:16px;letter-spacing:.2px}
.brand .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#6366f1,#a855f7);
  display:grid;place-items:center;font-size:16px}
.brand small{display:block;font-weight:400;font-size:11px;color:#94a3b8}
.nav{padding:8px}
.nav button{display:flex;align-items:center;gap:11px;width:100%;border:0;cursor:pointer;
  background:transparent;color:#cbd5e1;padding:11px 13px;border-radius:9px;font-size:14px;
  text-align:left;transition:.15s}
.nav button:hover{background:rgba(255,255,255,.06);color:#fff}
.nav button.active{background:var(--accent);color:#fff;font-weight:600;box-shadow:0 4px 12px rgba(79,70,229,.4)}
.nav .ico{width:18px;text-align:center}
.side .spacer{flex:1}
.conn{margin:12px;padding:13px;background:rgba(255,255,255,.05);border-radius:11px;font-size:12px}
.conn .row{display:flex;justify-content:space-between;margin:3px 0;color:#94a3b8}
.conn .row b{color:#e2e8f0;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot.on{background:var(--ok)} .dot.off{background:var(--warn)}
.conn button{margin-top:9px;width:100%;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.05);
  color:#e2e8f0;padding:7px;border-radius:8px;cursor:pointer;font-size:12px}
.conn button:hover{background:rgba(255,255,255,.12)}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.topbar{height:60px;background:var(--panel);border-bottom:1px solid var(--line);
  display:flex;align-items:center;padding:0 24px;gap:14px;flex-shrink:0}
.topbar h1{font-size:17px;margin:0;font-weight:650}
.topbar .sub{color:var(--muted);font-size:12px}
.stats{margin-left:auto;display:flex;gap:18px}
.stat{text-align:right} .stat b{font-size:15px;display:block} .stat span{font-size:11px;color:var(--muted)}
.view{flex:1;overflow:auto;padding:24px;display:none}
.view.active{display:block}
.view.nopad{padding:0;display:none} .view.nopad.active{display:flex}
.chatwrap{max-width:920px;margin:0 auto;display:flex;flex-direction:column;height:100%}
.msgs{flex:1;overflow:auto;padding:6px 2px 20px}
.msg{margin:16px 0;display:flex;gap:12px;animation:fade .25s}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
.msg .av{width:34px;height:34px;border-radius:9px;flex-shrink:0;display:grid;place-items:center;font-size:15px;color:#fff}
.msg.user .av{background:#334155} .msg.bot .av{background:linear-gradient(135deg,#6366f1,#a855f7)}
.bubble{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px 16px;
  box-shadow:var(--shadow);max-width:100%}
.msg.user .bubble{background:#334155;color:#fff;border:0}
.bubble .ans{white-space:pre-wrap}
.meta{display:flex;gap:8px;margin-top:11px;flex-wrap:wrap}
.chip{font-size:11px;padding:3px 9px;border-radius:20px;background:var(--chip);color:#475569;font-weight:600}
.chip.mode-local{background:#dbeafe;color:#1d4ed8}
.chip.mode-global{background:#f3e8ff;color:#7e22ce}
.chip.mode-mixed{background:#fef9c3;color:#a16207}
.srcbtn{margin-top:10px;font-size:12px;color:var(--accent);background:none;border:0;cursor:pointer;font-weight:600;padding:0}
.srcs{margin-top:10px;border-top:1px dashed var(--line);padding-top:10px;display:none}
.srcs.open{display:block}
.src{font-size:12.5px;padding:8px 10px;border:1px solid var(--line);border-radius:9px;margin:6px 0;
  cursor:pointer;transition:.12s;background:#fcfdff}
.src:hover{border-color:var(--accent);background:var(--accent-soft)}
.src .t{font-weight:600;color:#1e293b} .src .d{color:var(--muted);margin-top:2px}
.paths{margin-top:8px;font-size:12px;color:#475569}
.paths code{background:#f1f5f9;padding:2px 6px;border-radius:5px}
.composer{display:flex;gap:10px;padding:14px 2px;border-top:1px solid var(--line);background:#f8fafc}
.composer input{flex:1;border:1px solid var(--line);border-radius:11px;padding:13px 15px;font-size:14px;outline:none;background:#fff}
.composer input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.btn{background:var(--accent);color:#fff;border:0;border-radius:11px;padding:0 20px;font-weight:600;cursor:pointer;font-size:14px;height:44px}
.btn:hover{background:var(--accent2)} .btn:disabled{opacity:.5;cursor:default}
.btn.ghost{background:#fff;color:var(--accent);border:1px solid var(--line)}
.btn.danger{background:#fff;color:#dc2626;border:1px solid #fecaca}
.btn.danger:hover{background:#fef2f2;border-color:#fca5a5}
.hint{color:var(--muted);text-align:center;margin-top:60px}
.hint h2{color:#334155;font-weight:650} .examples{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:18px}
.ex{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;cursor:pointer;font-size:13px;max-width:260px;text-align:left;box-shadow:var(--shadow)}
.ex:hover{border-color:var(--accent);color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:var(--shadow);max-width:880px;margin:0 auto 18px}
.awsgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
.awsgrid label{display:flex;flex-direction:column;gap:5px;font-size:12px;font-weight:600;color:#475569}
.awsgrid input,.awsgrid select{border:1px solid var(--line);border-radius:9px;padding:9px 11px;font-size:13px;background:#fff}
.awswire{display:flex;flex-wrap:wrap;gap:7px;margin-top:6px}
.wchip{font-size:11.5px;background:#f1f5f9;border:1px solid var(--line);border-radius:20px;padding:4px 11px}
.wchip b{color:#0f172a} .wchip.aws{background:#fff7ed;border-color:#fdba74}
.awsrow{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;margin-top:8px;font-size:13px}
.awsrow .pill{font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px}
.awsrow.ok .pill{background:#dcfce7;color:#166534} .awsrow.bad .pill{background:#fee2e2;color:#991b1b}
.awsrow .cmp{font-weight:700;min-width:120px} .awsrow .dt{color:var(--muted);flex:1}
.awsrow .ms{color:#94a3b8;font-size:11px}
.muted{color:var(--muted);font-size:12px}
.drop{border:2px dashed #cbd5e1;border-radius:14px;padding:44px;text-align:center;color:var(--muted);transition:.15s;cursor:pointer}
.drop.over{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.drop .big{font-size:40px} .drop b{color:#334155;font-size:16px}
.uprow{display:flex;gap:10px;align-items:center;margin-top:16px;flex-wrap:wrap}
select{border:1px solid var(--line);border-radius:9px;padding:9px 11px;font-size:13px;background:#fff;height:44px}
.tbl{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
.tbl th{text-align:left;color:var(--muted);font-weight:600;padding:8px;border-bottom:2px solid var(--line)}
.tbl td{padding:8px;border-bottom:1px solid var(--line)}
.badge{font-size:11px;padding:2px 8px;border-radius:14px;font-weight:600}
.badge.create{background:#dcfce7;color:#15803d} .badge.update{background:#fef9c3;color:#a16207}
.badge.skipped{background:#e2e8f0;color:#475569} .badge.error{background:#fee2e2;color:#b91c1c}
.progress{height:6px;background:#e2e8f0;border-radius:6px;overflow:hidden;margin-top:14px;display:none}
.progress.show{display:block} .progress .bar{height:100%;background:var(--accent);width:0;transition:width .3s}
.gwrap{flex:1;display:flex;min-width:0}
.gcanvas{flex:1;position:relative;background:radial-gradient(circle at 50% 30%,#fbfdff,#f1f5f9)}
.gcanvas svg{width:100%;height:100%}
.gbar{position:absolute;top:14px;left:14px;display:flex;gap:8px;align-items:center;background:rgba(255,255,255,.9);
  padding:8px 10px;border-radius:11px;box-shadow:var(--shadow);backdrop-filter:blur(6px)}
.gbar input{border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;width:170px}
.gbar .btn{height:36px;padding:0 14px;font-size:13px}
.gbar select{height:36px}
.legend{position:absolute;bottom:14px;left:14px;background:rgba(255,255,255,.95);padding:11px 13px;border-radius:11px;box-shadow:var(--shadow);font-size:12px;max-height:46%;overflow:auto;max-width:230px;backdrop-filter:blur(4px)}
.legend .li{display:flex;align-items:center;gap:7px;margin:3px 0}
.legend .sw{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.legend .lgh{display:block;font-size:10px;letter-spacing:.05em;color:#94a3b8;font-weight:700;margin:2px 0 4px}
.legend .lgt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ginfo{width:320px;border-left:1px solid var(--line);background:var(--panel);padding:18px;overflow:auto;flex-shrink:0}
.ginfo h3{margin:0 0 4px} .ginfo .ty{color:var(--accent);font-size:12px;font-weight:600;text-transform:uppercase}
.ginfo .k{color:var(--muted);font-size:12px;margin-top:14px;font-weight:600;text-transform:uppercase;letter-spacing:.4px}
.modal{position:fixed;inset:0;background:rgba(15,23,42,.5);display:none;place-items:center;z-index:50}
.modal.show{display:grid}
.modal .box{background:#fff;border-radius:16px;padding:26px;width:420px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.modal h2{margin:0 0 4px} .modal p{color:var(--muted);margin:0 0 16px;font-size:13px}
.modal label{font-size:12px;font-weight:600;color:#475569} .modal input{width:100%;margin:5px 0 14px;
  border:1px solid var(--line);border-radius:9px;padding:10px;font-size:14px}
.modal .actions{display:flex;gap:10px;justify-content:flex-end}
/* ---- Document detail modal (KB end-to-end view) ---- */
.docbox{background:#fff;border-radius:16px;width:min(1180px,96vw);height:90vh;
  display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(0,0,0,.35);overflow:hidden}
.dochead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;
  padding:16px 20px;border-bottom:1px solid var(--line)}
.dochead h2{margin:0;font-size:16px;word-break:break-word}
.dochead .sub{color:var(--muted);font-size:12px;margin-top:3px}
.docbody{display:grid;grid-template-columns:minmax(360px,1fr) minmax(440px,1.1fr);
  flex:1;min-height:0}
.docprev{border-right:1px solid var(--line);display:flex;flex-direction:column;background:#f1f5f9}
.docprev .pgwrap{flex:1;overflow:auto;display:grid;place-items:start center;padding:14px}
.docprev img{max-width:100%;box-shadow:0 4px 18px rgba(0,0,0,.18);border-radius:4px;background:#fff}
.docprev .pgbar{display:flex;align-items:center;gap:8px;justify-content:center;
  padding:8px;border-top:1px solid var(--line);background:#fff;font-size:13px}
.docprev .pgbar button{border:1px solid var(--line);background:#fff;border-radius:8px;
  padding:4px 10px;cursor:pointer}
.docprev .noprev{color:var(--muted);font-size:13px;text-align:center;padding:30px}
.docdetail{display:flex;flex-direction:column;min-height:0}
.doctabs{display:flex;gap:4px;padding:10px 14px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.doctab{border:none;background:none;padding:8px 12px;cursor:pointer;font-size:13px;
  font-weight:600;color:var(--muted);border-bottom:2px solid transparent}
.doctab.active{color:var(--accent);border-bottom-color:var(--accent)}
.docpane{flex:1;overflow:auto;padding:16px;display:none}
.docpane.active{display:block}
.dgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.dcard{background:#f8fafc;border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.dcard .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.dcard .v{font-size:20px;font-weight:700;margin-top:2px}
.dcard .v small{font-size:12px;font-weight:500;color:var(--muted)}
.dbar{height:9px;border-radius:6px;overflow:hidden;display:flex;background:#e2e8f0;margin:8px 0}
.dbar i{height:100%}
.dlegend{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:#475569}
.dlegend span{display:flex;align-items:center;gap:5px}
.dlegend i{width:10px;height:10px;border-radius:3px;display:inline-block}
.dsec-title{font-size:12px;font-weight:700;color:#334155;text-transform:uppercase;
  letter-spacing:.04em;margin:16px 0 8px}
.chunkcard{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:9px}
.chunkcard:hover{border-color:var(--accent);cursor:pointer}
.chunkcard .ch-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.ctbadge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;color:#fff}
.ch-meta{font-size:11px;color:var(--muted)}
.chunkcard .ch-text{font-size:12.5px;color:#1e293b;white-space:pre-wrap;line-height:1.45;
  max-height:120px;overflow:auto}
.chunkcard .ch-sum{font-size:12px;color:#7c3aed;background:#f5f3ff;border-radius:8px;
  padding:6px 8px;margin-bottom:6px;white-space:pre-wrap;max-height:120px;overflow:auto}
.chunkcard .ch-ents{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.tagchip{font-size:11px;background:#eef2ff;color:#3730a3;border-radius:12px;padding:1px 8px}
.covgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.covitem{display:flex;align-items:center;gap:6px;font-size:12px;color:#475569}
.covitem i{width:8px;height:8px;border-radius:50%}
.kbtable tbody tr{cursor:pointer}
.kbtable tbody tr:hover{background:#f1f5f9}
.viewbtn{font-size:11px;color:var(--accent);font-weight:600}
/* ---- Processing pill + details modal ---- */
.procpill{display:flex;align-items:center;gap:8px;background:#eef2ff;color:#4338ca;
  border:1px solid #c7d2fe;border-radius:22px;padding:7px 14px;font-size:13px;
  font-weight:600;cursor:pointer;margin-left:auto;margin-right:16px}
.procpill:hover{background:#e0e7ff}
.procpill .procmore{color:#6366f1;font-weight:700}
.procbox{width:560px;max-width:92vw}
.prochead{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}
.proccur{background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.proccur .fn{font-weight:700;color:var(--ink);word-break:break-all}
.proccur .stg{display:inline-flex;align-items:center;gap:7px;margin-top:6px;font-size:13px;color:#4338ca}
.proccur .stagebadge{background:#eef2ff;border-radius:8px;padding:2px 9px;font-weight:600;text-transform:capitalize}
.procmetrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:8px}
.procmetrics .m{background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 10px;text-align:center}
.procmetrics .m b{display:block;font-size:18px;font-weight:800;color:var(--ink)}
.procmetrics .m span{font-size:11px;color:var(--muted)}
.proclog{max-height:200px;overflow:auto;margin-top:6px;border:1px solid var(--line);border-radius:10px}
.proclog .row{display:flex;justify-content:space-between;gap:8px;padding:7px 12px;border-bottom:1px solid #f1f5f9;font-size:12.5px}
.proclog .row:last-child{border-bottom:none}
.proclog .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.proclog .sc{color:var(--muted);flex-shrink:0}
.toast{position:fixed;bottom:22px;right:22px;background:#0f172a;color:#fff;padding:12px 16px;border-radius:11px;
  box-shadow:0 10px 30px rgba(0,0,0,.3);display:none;z-index:60;font-size:13px}
.spin{width:16px;height:16px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;
  display:inline-block;animation:sp .7s linear infinite;vertical-align:-3px}
@keyframes sp{to{transform:rotate(360deg)}}
/* ---- Knowledge Base ---- */
.kbwrap{max-width:1100px;margin:0 auto}
.kbhead{display:flex;justify-content:space-between;align-items:center;gap:16px;
  flex-wrap:wrap;margin-bottom:18px}
.kbsum{display:flex;gap:10px;flex-wrap:wrap}
.kbstat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:12px 18px;box-shadow:var(--shadow);min-width:104px}
.kbstat b{display:block;font-size:24px;font-weight:800;color:var(--ink);line-height:1.1}
.kbstat span{font-size:12px;color:var(--muted)}
.kbtools{display:flex;gap:8px;align-items:center}
.kbtools input{width:240px;padding:10px 12px;border:1px solid var(--line);
  border-radius:10px;font-size:14px;outline:none}
.kbtools input:focus{border-color:#6366f1;box-shadow:0 0 0 3px #6366f122}
.kbtable-wrap{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  box-shadow:var(--shadow);overflow:hidden}
.kbtable{width:100%;border-collapse:collapse;font-size:14px}
.kbtable thead th{text-align:left;padding:13px 16px;background:#f8fafc;
  color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.03em;border-bottom:1px solid var(--line);position:sticky;top:0}
.kbtable th.num,.kbtable td.num{text-align:right}
.kbtable tbody td{padding:12px 16px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.kbtable tbody tr:last-child td{border-bottom:none}
.kbtable tbody tr:hover{background:#f8fafc}
.kbname{font-weight:600;color:var(--ink);max-width:340px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.fdot{display:inline-block;width:8px;height:8px;border-radius:2px;background:#6366f1;
  margin-right:9px;vertical-align:middle}
.corp{background:#eef2ff;color:#4338ca;font-size:11px;font-weight:600;
  padding:3px 9px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em}
.ctchip{display:inline-flex;align-items:center;gap:5px;background:#f1f5f9;
  border-radius:20px;padding:3px 9px;font-size:11px;color:#475569;margin:2px 4px 2px 0;white-space:nowrap}
.ctchip i{width:8px;height:8px;border-radius:50%;display:inline-block}
.kbempty{padding:40px;text-align:center;color:var(--muted)}
</style>
</head>
<body>
<aside class="side">
  <div class="brand"><div class="logo">&#9670;</div><div>ATF GraphRAG<small>Knowledge Console</small></div></div>
  <nav class="nav">
    <button data-view="chat" class="active"><span class="ico">&#128172;</span> Chat</button>
    <button data-view="kb"><span class="ico">&#128218;</span> Knowledge Base</button>
    <button data-view="upload"><span class="ico">&#8593;</span> Upload</button>
    <button data-view="graph"><span class="ico">&#128376;</span> Graph</button>
    <button data-view="config"><span class="ico">&#129520;</span> Configuration</button>
    <button data-view="aws"><span class="ico">&#9729;</span> AWS Native</button>
  </nav>
  <div class="spacer"></div>
  <div class="conn">
    <div class="row"><span>Provider</span><b id="c-prov">&hellip;</b></div>
    <div class="row"><span>Model</span><b id="c-model">&hellip;</b></div>
    <div class="row"><span>API key</span><b id="c-key"><span class="dot off"></span>none</b></div>
    <button onclick="openKey()">&#9881; Configure connection</button>
  </div>
</aside>

<div class="main">
  <div class="topbar">
    <div><h1 id="t-title">Chat</h1><div class="sub" id="t-sub">Ask questions across your documents</div></div>
    <button id="procbtn" class="procpill" onclick="openProc()" style="display:none">
      <span class="spin"></span><span id="procbtntxt">Processing&hellip;</span>
      <span class="procmore">Details &rsaquo;</span>
    </button>
    <div class="stats">
      <div class="stat"><b id="s-chunks">0</b><span>chunks</span></div>
      <div class="stat"><b id="s-nodes">0</b><span>graph nodes</span></div>
      <div class="stat"><b id="s-comm">0</b><span>communities</span></div>
    </div>
  </div>

  <section class="view active" id="v-chat">
    <div class="chatwrap">
      <div class="msgs" id="msgs">
        <div class="hint" id="emptyhint">
          <div style="font-size:46px">&#128172;</div>
          <h2>Ask anything about your corpus</h2>
          <div>Answers are grounded in your documents, with sources and graph paths.</div>
          <div class="examples" id="examples"></div>
        </div>
      </div>
      <div class="composer">
        <input id="q" placeholder="Ask a question&hellip;  (e.g. How many firearms were manufactured in 2023?)" autocomplete="off"/>
        <button class="btn" id="send" onclick="ask()">Send</button>
      </div>
    </div>
  </section>

  <section class="view" id="v-kb">
    <div class="kbwrap">
      <div class="kbhead">
        <div class="kbsum">
          <div class="kbstat"><b id="kb-docs">0</b><span>documents</span></div>
          <div class="kbstat"><b id="kb-chunks">0</b><span>chunks</span></div>
          <div class="kbstat"><b id="kb-pages">0</b><span>pages</span></div>
          <div class="kbstat"><b id="kb-tables">0</b><span>table chunks</span></div>
        </div>
        <div class="kbtools">
          <input id="kbsearch" placeholder="&#128269; Filter documents&hellip;" oninput="renderKB()"/>
          <button class="btn ghost" onclick="loadKB()">&#8635; Refresh</button>
          <button class="btn" onclick="document.querySelector('[data-view=upload]').click()">+ Add documents</button>
          <select id="seed-pick" style="display:none;padding:9px 10px;border:1px solid var(--line);border-radius:9px;font-size:13px"></select>
          <button class="btn" id="seed-load" style="display:none" onclick="loadSeed(this)" title="Clear current data and load the selected seed knowledge base">&#9889; Load seed</button>
          <button class="btn ghost" onclick="saveSeed(this)" title="Snapshot the CURRENT knowledge base as a named seed">&#128190; Save as seed</button>
          <button class="btn ghost" onclick="fixTableTags(this)" title="Re-classify content types: demote number-dense prose mis-tagged as tables">&#129534; Fix table tags</button>
          <button class="btn ghost" onclick="doBackup(this)" title="Snapshot the vector index + graph">&#128190; Backup</button>
          <button class="btn ghost" onclick="doRestore(this)" title="Restore a previous snapshot">&#8635; Restore</button>
          <button class="btn danger" onclick="clearAll(this)">&#128465; Clear all data</button>
        </div>
      </div>
      <div class="kbtable-wrap">
        <table class="kbtable">
          <thead><tr>
            <th>Document</th><th>Corpus</th><th class="num">Pages</th>
            <th class="num">Chunks</th><th>Content</th><th>Extraction</th>
          </tr></thead>
          <tbody id="kbbody"></tbody>
        </table>
        <div class="kbempty" id="kbempty">No documents indexed yet. Use <b>Upload</b> to add files.</div>
      </div>
    </div>
  </section>

  <section class="view" id="v-upload">
    <div class="card">
      <h2 style="margin:0 0 4px">Add documents</h2>
      <p style="color:var(--muted);margin:0 0 16px">Drop files or a folder. PDFs, text, HTML and images are routed automatically (text / scanned / chart-heavy) and indexed into the graph + vector store.</p>
      <div class="drop" id="drop">
        <div class="big">&#128193;</div>
        <b>Drag &amp; drop files or a folder here</b>
        <div>or use the buttons below</div>
      </div>
      <div class="uprow">
        <button class="btn ghost" onclick="filePick.click()">Choose files</button>
        <button class="btn ghost" onclick="folderPick.click()">Choose folder</button>
        <select id="corpus">
          <option value="pdf">corpus: pdf</option>
          <option value="web">corpus: web</option>
          <option value="connected">corpus: connected</option>
          <option value="visual">corpus: visual</option>
        </select>
        <select id="upmode" title="Sync waits for each file; Async queues a background job for large batches">
          <option value="async">mode: async (background, recommended for many files)</option>
          <option value="sync">mode: sync (wait for results)</option>
        </select>
        <select id="upextract" title="LLM entity/relationship extraction. auto = only small docs (fast for bulk); on = every doc (richest graph, slow); off = none" onchange="setExtraction()">
          <option value="auto">extraction: auto (small docs only)</option>
          <option value="on">extraction: on (richest graph, slow)</option>
          <option value="off">extraction: off (fastest)</option>
        </select>
        <button class="btn" id="upbtn" onclick="doUpload()" disabled>Ingest 0 files</button>
        <span style="margin-left:auto;display:flex;gap:8px">
          <button class="btn ghost" onclick="verifyGraph(this)" title="LLM cross-verify: drop nodes that aren't real entities (dates, headers, noise)">&#129529; Clean graph</button>
          <button class="btn ghost" onclick="buildCommunities(this)">&#8635; Rebuild communities</button></span>
      </div>
      <input type="file" id="filePick" multiple style="display:none"/>
      <input type="file" id="folderPick" webkitdirectory multiple style="display:none"/>
      <div class="progress" id="prog"><div class="bar" id="bar"></div></div>
      <table class="tbl" id="uptbl" style="display:none">
        <thead><tr><th>File</th><th>Route</th><th>Status</th><th style="text-align:right">Chunks</th></tr></thead>
        <tbody id="uptbody"></tbody>
      </table>
    </div>
  </section>

  <section class="view nopad" id="v-graph">
    <div class="gwrap">
      <div class="gcanvas" id="gcanvas">
        <div class="gbar">
          <button class="btn" onclick="loadGraph()">&#8635; Load graph</button>
          <input id="gsearch" placeholder="Search entity&hellip;" oninput="filterGraph()"/>
          <select id="gtype" onchange="filterGraph()"><option value="">all types</option></select>
        </div>
        <div class="legend" id="legend"></div>
      </div>
      <div class="ginfo" id="ginfo">
        <div style="color:var(--muted);text-align:center;margin-top:40px">
          <div style="font-size:40px">&#128376;</div>
          Click <b>Load graph</b>, then click any node to inspect its connections and source documents.
        </div>
      </div>
    </div>
  </section>

  <section class="view" id="v-config">
    <div class="card">
      <h2 style="margin:0 0 4px">Compose your RAG &mdash; building blocks</h2>
      <p style="color:var(--muted);margin:0 0 14px;font-size:13px">Every component is swappable. Pick a provider per block, or load a deployment preset, then <b>Apply</b> to switch the live engine. Runtime blocks take effect instantly; embeddings / vector / graph stores change the data space, so they need a re-ingest or import.</p>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
        <label class="s" style="font-weight:600">Preset:</label>
        <select id="cfg-preset" style="padding:9px 12px;border:1px solid var(--line);border-radius:9px"></select>
        <button class="btn ghost" onclick="loadPreset()">Load preset</button>
        <span style="margin-left:auto"></span>
        <button class="btn" onclick="applyConfig(this)">&#9889; Apply configuration</button>
      </div>
      <div id="cfg-blocks"></div>
      <div id="cfg-result" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 8px">Live wiring</h3>
      <div id="cfg-wiring" class="awswire"></div>
    </div>
  </section>

  <section class="view" id="v-aws">
    <div class="card">
      <h2 style="margin:0 0 4px">AWS-native stack</h2>
      <p style="color:var(--muted);margin:0 0 8px">Configure credentials and managed
      endpoints, validate live connectivity per component, switch the running
      engine onto Bedrock + managed stores, then run an end-to-end smoke test.
      <b>Secrets are held in memory only — never written to disk or git.</b></p>
      <div id="aws-wiring" class="awswire"></div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px">1 &middot; Credentials</h3>
      <div class="awsgrid">
        <label>Region<input id="aws-region" placeholder="us-east-1" value="us-east-1"/></label>
        <label>Access key ID<input id="aws-akid" placeholder="AKIA&hellip;"/></label>
        <label>Secret access key<input id="aws-secret" type="password" placeholder="&bull;&bull;&bull;"/></label>
        <label>Session token (optional)<input id="aws-token" type="password" placeholder="optional"/></label>
        <label>Neo4j/Neptune user<input id="aws-n4user" placeholder="neo4j"/></label>
        <label>Neo4j/Neptune password<input id="aws-n4pass" type="password" placeholder="&bull;&bull;&bull;"/></label>
      </div>
      <div class="actions" style="justify-content:flex-start">
        <button class="btn" onclick="awsSaveCreds()">Save credentials (in-memory)</button>
        <span id="aws-credstate" class="muted"></span>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px">2 &middot; Components</h3>
      <div class="awsgrid">
        <label>Bedrock LLM model<input id="aws-llm" value="anthropic.claude-3-5-sonnet-20240620-v1:0"/></label>
        <label>Bedrock vision model<input id="aws-vision" value="anthropic.claude-3-5-sonnet-20240620-v1:0"/></label>
        <label>Titan embedding model<input id="aws-emb" value="amazon.titan-embed-text-v2:0"/></label>
        <label>Embedding dim<input id="aws-embdim" value="1024"/></label>
        <label>Reranker (Cohere)<input id="aws-rr" value="cohere.rerank-v3-5:0"/></label>
        <label>Textract OCR<select id="aws-ocr"><option value="1">enabled</option><option value="0">disabled</option></select></label>
      </div>
      <div class="awsgrid" style="margin-top:6px">
        <label>Vector store<select id="aws-vs" onchange="awsVsFields()"><option value="opensearch">OpenSearch</option><option value="qdrant">Qdrant</option></select></label>
        <label id="aws-vs-ep-l">OpenSearch host<input id="aws-vs-ep" placeholder="https://search-xxx.us-east-1.es.amazonaws.com"/></label>
        <label id="aws-vs-key-l" style="display:none">Qdrant API key<input id="aws-vs-key" type="password" placeholder="optional"/></label>
        <label>Index/collection prefix<input id="aws-vs-prefix" value="atf"/></label>
      </div>
      <div class="awsgrid" style="margin-top:6px">
        <label>Graph store<select id="aws-gs" onchange="awsGsFields()"><option value="neptune">Neptune</option><option value="neo4j">Neo4j</option></select></label>
        <label id="aws-gs-ep-l">Neptune endpoint<input id="aws-gs-ep" placeholder="db-neptune-1.cluster-xxx.us-east-1.neptune.amazonaws.com"/></label>
        <label id="aws-gs-port-l">Port<input id="aws-gs-port" value="8182"/></label>
        <label id="aws-gs-uri-l" style="display:none">Neo4j URI<input id="aws-gs-uri" placeholder="bolt://host:7687"/></label>
      </div>
      <div class="awsgrid" style="margin-top:6px">
        <label>S3 bucket<input id="aws-s3" placeholder="my-atf-bucket"/></label>
        <label>S3 prefix<input id="aws-s3prefix" placeholder="rag/"/></label>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 4px">3 &middot; Advanced Bedrock (BDA &middot; Guardrails+Automated Reasoning &middot; RAG Eval)</h3>
      <p style="color:var(--muted);margin:0 0 10px;font-size:13px">The managed Bedrock capabilities. All optional &mdash; leave blank to skip.</p>
      <div class="awsgrid">
        <label>Document parser
          <select id="aws-parser">
            <option value="bedrock">Bedrock FM (Claude)</option>
            <option value="bda">Bedrock Data Automation (BDA)</option>
            <option value="textract">Textract (structured)</option>
            <option value="docling">Docling (local)</option>
          </select></label>
        <label>BDA project ARN<input id="aws-bda-arn" placeholder="arn:aws:bedrock:...:data-automation-project/..."/></label>
        <label>BDA working bucket<input id="aws-bda-bucket" placeholder="my-bda-bucket"/></label>
      </div>
      <div class="awsgrid" style="margin-top:6px">
        <label>Guardrail<select id="aws-gr-en"><option value="0">disabled</option><option value="1">enabled</option></select></label>
        <label>Guardrail ID<input id="aws-gr-id" placeholder="abcd1234"/></label>
        <label>Guardrail version<input id="aws-gr-ver" value="DRAFT"/></label>
        <label>Automated Reasoning policy ARN<input id="aws-ar-arn" placeholder="arn:aws:bedrock:...:automated-reasoning-policy/..."/></label>
      </div>
      <div style="margin-top:12px;border-top:1px solid var(--line);padding-top:10px">
        <h4 style="margin:0 0 6px">Managed RAG Evaluation</h4>
        <div class="awsgrid">
          <label>Eval service role ARN<input id="aws-ev-role" placeholder="arn:aws:iam::...:role/bedrock-eval"/></label>
          <label>Dataset (S3 JSONL)<input id="aws-ev-data" placeholder="s3://bucket/qa.jsonl"/></label>
          <label>Output (S3)<input id="aws-ev-out" placeholder="s3://bucket/eval-out/"/></label>
        </div>
        <div class="actions" style="justify-content:flex-start;margin-top:8px">
          <button class="btn ghost" onclick="awsRagEval(this)">&#128202; Submit RAG evaluation job</button>
        </div>
        <div id="aws-ev-res" class="s" style="margin-top:6px"></div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 4px">4 &middot; Provision / tear down the AWS-native stack</h3>
      <p style="color:var(--muted);margin:0 0 10px;font-size:13px">Create the managed resources (S3, DynamoDB, SSM, Bedrock Guardrail, OpenSearch Serverless, Neptune Analytics) from here — or <b>delete everything</b> in one click to stop paying when you're done. Resources are tagged <code>Project=atf-graphrag</code>. Run <b>Plan</b> first.</p>
      <div class="awsgrid" style="margin-bottom:8px">
        <label>Stack project tag<input id="aws-proj" value="atf-graphrag"/></label>
        <label>Region<input id="aws-proj-region" value="us-east-1"/></label>
      </div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap">
        <button class="btn ghost" onclick="awsPlan('provision')">&#128196; Plan provision</button>
        <button class="btn" onclick="awsProvision()">&#9729; Provision all</button>
        <button class="btn ghost" onclick="awsInventory()">&#128269; Inventory &amp; cost</button>
        <button class="btn danger" onclick="awsTeardown()">&#128465; Delete ALL AWS resources</button>
      </div>
      <div id="aws-prov" style="margin-top:10px"></div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px">5 &middot; Validate &amp; activate</h3>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap">
        <button class="btn" onclick="awsValidate()">&#128268; Validate connectivity</button>
        <button class="btn" onclick="awsApply()">&#9889; Apply &amp; switch engine</button>
        <button class="btn" onclick="awsSmoke()">&#9654; Run end-to-end smoke test</button>
        <button class="btn ghost" onclick="awsRevert()">&#8617; Revert to local</button>
      </div>
      <div id="aws-results"></div>
      <div id="aws-smoke"></div>
    </div>
  </section>
</div>

<div class="modal" id="keymodal">
  <div class="box">
    <h2>Connection</h2>
    <p>Set an OpenRouter API key for full LLM generation &amp; extraction. Without it the app runs in offline (extractive) mode.</p>
    <label>OpenRouter API key</label>
    <input id="keyin" type="password" placeholder="sk-or-v1-&hellip;"/>
    <label>Model</label>
    <input id="modelin" placeholder="openai/gpt-4o-mini"/>
    <div class="actions">
      <button class="btn ghost" onclick="closeKey()">Cancel</button>
      <button class="btn" onclick="saveKey()">Save</button>
    </div>
  </div>
</div>
<div class="modal" id="procmodal">
  <div class="box procbox">
    <div class="prochead">
      <div><h2 style="margin:0">Processing details</h2>
        <p style="margin:2px 0 0" id="proc-job">&hellip;</p></div>
      <div style="display:flex;gap:8px">
        <button class="btn danger" id="proc-stop" onclick="cancelJob(this)">&#9632; Stop processing</button>
        <button class="btn ghost" onclick="closeProc()">Close</button>
      </div>
    </div>
    <div class="proccur" id="proc-current"></div>
    <div class="progress show" style="margin:14px 0 6px"><div class="bar" id="proc-bar"></div></div>
    <div class="procmetrics" id="proc-metrics"></div>
    <div class="k" style="margin-top:14px;font-size:11px;color:#94a3b8;font-weight:700;letter-spacing:.04em">RECENT FILES</div>
    <div class="proclog" id="proc-log"></div>
  </div>
</div>
<div class="modal" id="docmodal">
  <div class="docbox">
    <div class="dochead">
      <div>
        <h2 id="doc-title">&hellip;</h2>
        <div class="sub" id="doc-sub"></div>
      </div>
      <div style="display:flex;gap:8px">
        <a class="btn ghost" id="doc-open" target="_blank" rel="noopener">&#11015; Open PDF</a>
        <button class="btn ghost" onclick="closeDoc()">Close</button>
      </div>
    </div>
    <div class="docbody">
      <div class="docprev">
        <div class="pgwrap" id="doc-pgwrap"><div class="noprev">Loading preview&hellip;</div></div>
        <div class="pgbar" id="doc-pgbar" style="display:none">
          <button onclick="docPage(-1)">&#8249; Prev</button>
          <span id="doc-pgnum">1 / 1</span>
          <button onclick="docPage(1)">Next &#8250;</button>
        </div>
      </div>
      <div class="docdetail">
        <div class="doctabs">
          <button class="doctab active" data-pane="parsed" onclick="docTab('parsed')">Parsed</button>
          <button class="doctab" data-pane="ingested" onclick="docTab('ingested')">Ingested</button>
          <button class="doctab" data-pane="chunks" onclick="docTab('chunks')">Chunks</button>
          <button class="doctab" data-pane="indexed" onclick="docTab('indexed')">Indexed</button>
        </div>
        <div class="docpane active" id="pane-parsed"></div>
        <div class="docpane" id="pane-ingested"></div>
        <div class="docpane" id="pane-chunks"></div>
        <div class="docpane" id="pane-indexed"></div>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $=s=>document.querySelector(s);
let pending=[];
function toast(m,ms=2600){const t=$('#toast');t.textContent=m;t.style.display='block';clearTimeout(t._);t._=setTimeout(()=>t.style.display='none',ms);}

document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  const v=b.dataset.view;
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
  $('#v-'+v).classList.add('active');
  const titles={chat:['Chat','Ask questions across your documents'],
    kb:['Knowledge Base','Documents currently indexed in the corpus'],
    upload:['Upload','Add files and folders to the knowledge base'],
    graph:['Graph','Explore entities, communities and connections'],
    config:['Configuration','Compose your RAG from swappable building blocks'],
    aws:['AWS Native','Configure &amp; test the AWS-native stack end to end']};
  $('#t-title').textContent=titles[v][0]; $('#t-sub').textContent=titles[v][1];
  if(v==='kb') loadKB();
  if(v==='aws') awsStatus();
  if(v==='config') loadConfig();
});

async function refresh(){
  try{
    const s=await fetch('/api/status').then(r=>r.json());
    $('#c-prov').textContent=(s.llm||'').split(':')[0]||'offline';
    $('#c-model').textContent=(s.llm||'').split(':').slice(1).join(':')||'—';
    $('#c-key').innerHTML='<span class="dot '+(s.key_set?'on':'off')+'"></span>'+(s.key_set?'connected':'none');
    if(s.llm_extraction){const ex=$('#upextract');if(ex)ex.value=s.llm_extraction;}
    const corp=s.corpora||{}; const chunks=Object.values(corp).reduce((a,b)=>a+b,0);
    $('#s-chunks').textContent=chunks.toLocaleString();
    $('#s-nodes').textContent=((s.graph||{}).nodes||0).toLocaleString();
  }catch(e){}
  try{const g=await fetch('/graph/export').then(r=>r.json());$('#s-comm').textContent=(g.stats||{}).communities||0;}catch(e){}
}
refresh();

// ---- Knowledge Base ----
let KBDOCS=[];
const CT_COLORS={text:'#64748b',table:'#0ea5e9',figure:'#8b5cf6',chart:'#f59e0b',list:'#10b981',
  vision:'#ec4899',ocr:'#ef4444'};
function chip(label,n,total){
  const pct=total?Math.round(n/total*100):0;
  const col=CT_COLORS[label]||'#94a3b8';
  return '<span class="ctchip" title="'+label+': '+n+'"><i style="background:'+col+'"></i>'+
         label+' '+pct+'%</span>';
}
async function loadKB(){
  try{
    const d=await fetch('/api/documents').then(r=>r.json());
    KBDOCS=d.documents||[];
    $('#kb-docs').textContent=(d.total_documents||0).toLocaleString();
    $('#kb-chunks').textContent=(d.total_chunks||0).toLocaleString();
    let pages=0,tbl=0;
    KBDOCS.forEach(x=>{pages+=x.page_count||0; tbl+=(x.content_types||{}).table||0;});
    $('#kb-pages').textContent=pages.toLocaleString();
    $('#kb-tables').textContent=tbl.toLocaleString();
    renderKB(); checkSeed();
  }catch(e){ $('#kbempty').style.display='block'; }
}
async function clearAll(btn){
  if(!confirm('Permanently delete ALL data — every document, the vector index, '+
    'the knowledge graph, communities and caches? This cannot be undone.')) return;
  const o=btn.innerHTML; btn.disabled=true; btn.innerHTML='Clearing&hellip;';
  try{
    const r=await fetch('/api/clear',{method:'POST'}).then(r=>r.json());
    toast('All data cleared ('+(r.cleared||[]).join(', ')+')');
    KBDOCS=[]; loadKB(); refresh();
  }catch(e){ toast('Clear failed'); }
  btn.disabled=false; btn.innerHTML=o;
}
async function fixTableTags(btn){
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Re-classifying&hellip;';
  try{const r=await fetch('/api/reclassify',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
    toast('Re-classified: '+(r.demoted_table||0)+' prose chunks demoted from table, '+(r.kept_table||0)+' real tables kept');
    loadKB();}
  catch(e){toast('Re-classify failed');}
  btn.disabled=false;btn.innerHTML=o;
}
async function doBackup(btn){
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Backing up&hellip;';
  try{const r=await fetch('/api/backup',{method:'POST'}).then(r=>r.json());
    toast('Backup created: '+r.name+' ('+Math.round((r.bytes||0)/1024)+' KB)');}
  catch(e){toast('Backup failed');}
  btn.disabled=false;btn.innerHTML=o;
}
async function doRestore(btn){
  let list;try{list=(await fetch('/api/backups').then(r=>r.json())).backups||[];}
  catch(e){return toast('Could not list backups');}
  if(!list.length)return toast('No backups yet — create one first');
  const names=list.map((b,i)=>(i+1)+'. '+b.name).join('\n');
  const pick=prompt('Restore which backup? Enter the number:\n'+names,'1');
  if(!pick)return;
  const b=list[parseInt(pick,10)-1];if(!b)return toast('Invalid choice');
  if(!confirm('Restore '+b.name+'? This replaces the current index + graph.'))return;
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Restoring&hellip;';
  try{const r=await fetch('/api/restore',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:b.name})}).then(r=>r.json());
    toast(r.ok?('Restored — '+r.documents+' documents'):'Restore failed');loadKB();refresh();}
  catch(e){toast('Restore failed');}
  btn.disabled=false;btn.innerHTML=o;
}
async function checkSeed(){
  try{const s=await fetch('/api/seeds').then(r=>r.json());
    const seeds=s.seeds||[]; const pick=$('#seed-pick'), btn=$('#seed-load');
    if(seeds.length){
      pick.innerHTML=seeds.map(z=>{
        const lbl=z.name+(z.graph_nodes?(' · '+z.documents+' docs · '+z.graph_nodes+' nodes'+
          (z.communities?'/'+z.communities+'c':'')):'')+(z.note?' — '+z.note:'');
        return '<option value="'+esc(z.name)+'">'+esc(lbl)+'</option>';}).join('');
      pick.style.display=''; btn.style.display='';
    }else{pick.style.display='none'; btn.style.display='none';}
  }catch(e){}
}
async function saveSeed(btn){
  const name=(prompt('Save current KB as seed named:','new')||'').trim();
  if(!name)return;
  const note=(prompt('Short note for this seed (optional):','')||'').trim();
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Saving seed&hellip;';
  try{const r=await fetch('/api/seed/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,note})}).then(r=>r.json());
    toast('Seed "'+r.name+'" saved: '+r.documents+' docs · '+(r.graph_nodes||0)+' nodes ('+
      Math.round((r.bytes||0)/1024/1024)+' MB)');
    checkSeed();}
  catch(e){toast('Save seed failed');}
  btn.disabled=false;btn.innerHTML=o;
}
async function loadSeed(btn){
  const name=$('#seed-pick').value;
  if(!name)return;
  if(!confirm('Load seed "'+name+'"? This CLEARS the current knowledge base and '+
    'restores that frozen snapshot (ingestion + indexing already done — retrieval ready immediately).'))return;
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Loading seed&hellip;';
  try{const r=await fetch('/api/seed/restore',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name})}).then(r=>r.json());
    toast(r.ok?('Seed "'+name+'" loaded — '+r.documents+' documents ready'):'Seed not found');
    loadKB();refresh();}
  catch(e){toast('Load seed failed');}
  btn.disabled=false;btn.innerHTML=o;
}
function renderKB(){
  const q=($('#kbsearch').value||'').toLowerCase();
  const rows=KBDOCS.filter(d=>d.name.toLowerCase().includes(q));
  const tb=$('#kbbody'); tb.innerHTML='';
  $('#kbempty').style.display = rows.length? 'none':'block';
  rows.forEach(d=>{
    const tot=d.chunks||1;
    const cts=Object.entries(d.content_types||{}).sort((a,b)=>b[1]-a[1])
      .map(([k,v])=>chip(k,v,tot)).join('');
    const meth=Object.entries(d.methods||{}).sort((a,b)=>b[1]-a[1])
      .map(([k,v])=>chip(k,v,tot)).join('');
    const tr=document.createElement('tr');
    tr.innerHTML='<td class="kbname" title="Click to inspect — '+esc(d.name)+'">'+
        '<span class="fdot"></span>'+esc(d.name)+' <span class="viewbtn">&rsaquo; view</span></td>'+
      '<td><span class="corp">'+esc(d.corpus)+'</span></td>'+
      '<td class="num">'+(d.page_count||'—')+'</td>'+
      '<td class="num"><b>'+d.chunks.toLocaleString()+'</b></td>'+
      '<td>'+cts+'</td><td>'+meth+'</td>';
    tr.onclick=()=>openDoc(d.corpus,d.document_id,d.name);
    tb.appendChild(tr);
  });
}

// ---- Document end-to-end detail ----
let DOC=null, DOCPAGE=1;
const METHOD_LABEL={text:'Text layer',vision:'Vision/VLM',ocr:'OCR',
  table_extraction:'Table extraction',table:'Table extraction',web:'Web'};
function openDoc(corpus,doc_id,name){
  $('#docmodal').classList.add('show');
  $('#doc-title').textContent=name; $('#doc-sub').textContent='Loading…';
  ['parsed','ingested','chunks','indexed'].forEach(p=>$('#pane-'+p).innerHTML='');
  $('#doc-pgwrap').innerHTML='<div class="noprev">Loading preview…</div>';
  $('#doc-pgbar').style.display='none';
  const qs='corpus='+encodeURIComponent(corpus)+'&doc_id='+encodeURIComponent(doc_id||'')+
           '&name='+encodeURIComponent(name||'');
  fetch('/api/document?'+qs).then(r=>r.json()).then(det=>{
    if(det.error){$('#doc-sub').textContent='Not found';return;}
    DOC=det; DOC._qs=qs; DOCPAGE=(det.file.page_list||[1])[0]||1;
    renderDoc(det); docTab('parsed');
  }).catch(()=>{$('#doc-sub').textContent='Failed to load detail';});
}
function closeDoc(){$('#docmodal').classList.remove('show');DOC=null;}
function docTab(p){
  document.querySelectorAll('.doctab').forEach(t=>t.classList.toggle('active',t.dataset.pane===p));
  document.querySelectorAll('.docpane').forEach(x=>x.classList.remove('active'));
  $('#pane-'+p).classList.add('active');
}
function statCard(k,v,sub){return '<div class="dcard"><div class="k">'+k+'</div>'+
  '<div class="v">'+v+(sub?' <small>'+sub+'</small>':'')+'</div></div>';}
function ctBar(cts,total){
  const order=['text','table','chart','figure','list'];
  const segs=order.filter(k=>cts[k]).map(k=>'<i style="width:'+(cts[k]/total*100)+
    '%;background:'+(CT_COLORS[k]||'#94a3b8')+'"></i>').join('');
  const leg=order.filter(k=>cts[k]).map(k=>'<span><i style="background:'+(CT_COLORS[k]||'#94a3b8')+
    '"></i>'+k+' '+cts[k]+'</span>').join('');
  return '<div class="dbar">'+segs+'</div><div class="dlegend">'+leg+'</div>';
}
function renderDoc(det){
  const f=det.file, p=det.parsed, ing=det.ingested, ix=det.indexed;
  $('#doc-sub').textContent=f.corpus.toUpperCase()+' · '+f.pages+' pages · '+
    det.chunk_total.toLocaleString()+' chunks · doc '+f.document_id;
  const open=$('#doc-open');
  if(f.preview_available){open.style.display='';open.href='/api/document/file?'+DOC._qs;}
  else open.style.display='none';
  // preview
  if(f.preview_available){
    $('#doc-pgbar').style.display='flex'; showPage(DOCPAGE);
  }else{
    $('#doc-pgwrap').innerHTML='<div class="noprev">Original file not found on disk for preview.<br><br>'+
      'Set <b>ATF_PREVIEW_ROOTS</b> to the folder you ingested from (or re-upload) to enable page rendering.<br><br>'+
      'All parsing / ingestion / indexing detail is shown on the right.</div>';
  }
  // ---- Parsed ----
  $('#pane-parsed').innerHTML=
    '<div class="dgrid">'+
      statCard('Parser',p.parser)+
      statCard('OCR engine',p.ocr)+
      statCard('Pages',f.pages)+
      statCard('Vision model',(p.vision_models[0]||'—'))+
    '</div>'+
    '<div class="dsec-title">How each chunk was extracted</div>'+
    Object.entries(p.methods).sort((a,b)=>b[1]-a[1]).map(([k,v])=>
      '<div class="covitem" style="margin:4px 0"><i style="background:'+(CT_COLORS[k]||'#64748b')+
      '"></i><b>'+v+'</b>&nbsp;chunks &middot; '+(METHOD_LABEL[k]||k)+'</div>').join('')+
    '<div class="dsec-title">What this means</div>'+
    '<div style="font-size:12.5px;color:#475569;line-height:1.5">Text-layer chunks come straight '+
    'from the PDF text. <b style="color:#ec4899">Vision/VLM</b> chunks are pages/figures rendered to an '+
    'image and described by the vision model. <b style="color:#0ea5e9">Table</b> chunks are reconstructed '+
    'into Markdown rows/columns. <b style="color:#ef4444">OCR</b> chunks come from scanned pages.</div>';
  // ---- Ingested ----
  $('#pane-ingested').innerHTML=
    '<div class="dgrid">'+
      statCard('Total chunks',det.chunk_total.toLocaleString())+
      statCard('Tables',ing.tables)+
      statCard('Charts',ing.charts)+
      statCard('Figures',ing.figures)+
    '</div>'+
    '<div class="dsec-title">Content mix</div>'+ctBar(ing.content_types,det.chunk_total)+
    '<div class="dsec-title">Visual handling</div>'+
    '<div class="dgrid">'+
      statCard('Vision/VLM chunks',ing.vision_chunks)+
      statCard('Table-extracted',ing.table_extracted)+
      statCard('OCR chunks',ing.ocr_chunks)+
      statCard('Plain text',ing.text)+
    '</div>';
  // ---- Indexed ----
  const fc=ix.field_coverage||{};
  const cov=Object.keys(fc).map(k=>'<div class="covitem"><i style="background:'+
    (fc[k]?'#10b981':'#cbd5e1')+'"></i>'+k+'</div>').join('');
  $('#pane-indexed').innerHTML=
    '<div class="dgrid">'+
      statCard('Vectors',ix.vector_count.toLocaleString(),'@ '+ix.embedding_dim+'d')+
      statCard('Embedding',ix.embedding_model)+
      statCard('Entities',ix.unique_entities)+
      statCard('Relationships',ix.relationships)+
    '</div>'+
    '<div class="dgrid">'+
      statCard('Graph nodes',ix.graph_nodes_present)+
      statCard('Graph edges',ix.graph_edges_present)+
      statCard('Metadata fields',ix.metadata_fields_populated+' / '+ix.metadata_fields_total)+
      statCard('In corpus',f.corpus)+
    '</div>'+
    (ix.entity_sample.length?'<div class="dsec-title">Entities extracted &rarr; graph</div><div class="ch-ents">'+
      ix.entity_sample.map(e=>'<span class="tagchip">'+esc(e)+'</span>').join('')+'</div>':'')+
    (ix.relationship_sample.length?'<div class="dsec-title">Relationships</div><div class="ch-ents">'+
      ix.relationship_sample.map(r=>'<span class="tagchip">'+esc((r.source||'')+' →'+(r.relation||'rel')+'→ '+(r.target||''))+'</span>').join('')+'</div>':'')+
    '<div class="dsec-title">Metadata field coverage ('+ix.metadata_fields_populated+'/'+ix.metadata_fields_total+' populated)</div>'+
    '<div class="covgrid">'+cov+'</div>';
  // ---- Chunks ----
  $('#pane-chunks').innerHTML=
    '<div style="font-size:12px;color:var(--muted);margin-bottom:8px">Showing '+det.chunk_shown+
    ' of '+det.chunk_total+' chunks · click a chunk to jump the preview to its page</div>'+
    det.chunks.map(c=>{
      const col=CT_COLORS[c.content_type]||'#64748b';
      const ents=(c.entities||[]).map(e=>'<span class="tagchip">'+esc(e)+'</span>').join('');
      return '<div class="chunkcard" onclick="showPage('+(c.page||1)+')">'+
        '<div class="ch-top"><span class="ctbadge" style="background:'+col+'">'+c.content_type+'</span>'+
        '<span class="ch-meta">page '+(c.page||'—')+' · '+(METHOD_LABEL[c.method]||c.method)+
        (c.vision_model?' · '+c.vision_model:'')+' · '+c.chars+' chars'+
        (c.section?' · '+esc(c.section):'')+'</span></div>'+
        (c.summary?'<div class="ch-sum">'+esc(c.summary)+'</div>':'')+
        '<div class="ch-text">'+esc(c.text)+(c.truncated?' …':'')+'</div>'+
        (ents?'<div class="ch-ents">'+ents+'</div>':'')+'</div>';
    }).join('');
}
function showPage(n){
  if(!DOC||!DOC.file.preview_available)return;
  const pl=DOC.file.page_list||[1];
  DOCPAGE=n;
  $('#doc-pgwrap').innerHTML='<img id="doc-img" src="/api/document/page?'+DOC._qs+'&page='+n+
    '" onerror="this.parentNode.innerHTML=\'<div class=noprev>Could not render this page.</div>\'"/>';
  $('#doc-pgnum').textContent='page '+n;
}
function docPage(delta){
  if(!DOC)return;
  const pl=DOC.file.page_list||[];
  let n=DOCPAGE+delta;
  if(pl.length){const i=pl.indexOf(DOCPAGE); const j=Math.max(0,Math.min((i<0?0:i)+delta,pl.length-1)); n=pl[j];}
  showPage(n);
}

function openKey(){$('#keymodal').classList.add('show');}
function closeKey(){$('#keymodal').classList.remove('show');}

// ---- Processing visibility (global) ----
let ACTIVE=null, procOpen=false;
function fmtETA(s){if(s==null)return '—';s=Math.round(s);if(s<60)return s+'s';
  const m=Math.floor(s/60);return m+'m '+(s%60)+'s';}
async function pollActive(){
  try{
    const j=await fetch('/api/jobs/active').then(r=>r.json());
    ACTIVE=(j&&j.id)?j:null;
    const btn=$('#procbtn');
    if(ACTIVE){
      const st=ACTIVE.stats||{}, proc=(ACTIVE.done||0)+(ACTIVE.failed||0);
      $('#procbtntxt').textContent='Processing '+proc+'/'+(ACTIVE.total||0)
        +(st.eta_s!=null?(' · ~'+fmtETA(st.eta_s)+' left'):'');
      btn.style.display='flex';
    }else{ btn.style.display='none'; if(procOpen)renderProc(); }
  }catch(e){}
  if(procOpen)renderProc();
  setTimeout(pollActive, ACTIVE?1200:3000);
}
function openProc(){procOpen=true;$('#procmodal').classList.add('show');renderProc();}
function closeProc(){procOpen=false;$('#procmodal').classList.remove('show');}
let PROCJID=null;
async function cancelJob(btn){
  if(!PROCJID)return;
  if(!confirm('Stop processing this job? Queued files are skipped and the current file aborts at its next page.'))return;
  btn.disabled=true;btn.innerHTML='Stopping&hellip;';
  try{await fetch('/api/jobs/'+PROCJID+'/cancel',{method:'POST'});toast('Cancelling&hellip;');}
  catch(e){toast('Cancel failed');}
  renderProc();
}
async function renderProc(){
  let j=ACTIVE;
  if(!j){ // fetch the most recent job even if finished, so the modal shows results
    try{const l=await fetch('/api/jobs').then(r=>r.json());j=(l.jobs||[])[0];}catch(e){}
  } else { try{j=await fetch('/api/jobs/'+j.id).then(r=>r.json());}catch(e){} }
  if(!j){$('#proc-current').innerHTML='<span class="muted">No active job.</span>';return;}
  const st=j.stats||{}, cur=j.current, proc=(j.done||0)+(j.failed||0);
  PROCJID=j.id;
  const running=!['completed','cancelled'].includes(j.status);
  const sb=$('#proc-stop');
  if(sb){sb.style.display=running?'inline-flex':'none';
    if(j.status==='cancelling'){sb.disabled=true;sb.innerHTML='Stopping&hellip;';}
    else if(running){sb.disabled=false;sb.innerHTML='&#9632; Stop processing';}}
  $('#proc-job').textContent='Job '+j.id+' · '+j.status+' · corpus '+(j.corpus||'pdf')
    +((j.cancelled_count)?(' · '+j.cancelled_count+' skipped'):'');
  // current file + stage
  if(cur){
    const stg=cur.stage||'working';
    const pg=(cur.pages? (' page '+cur.page+'/'+cur.pages):'');
    $('#proc-current').innerHTML='<div class="fn">'+esc(cur.name||'')+'</div>'
      +'<div class="stg"><span class="spin"></span><span class="stagebadge">'+esc(stg)+'</span>'
      +esc(pg)+' &middot; '+(cur.chunks||0)+' chunks so far</div>';
  } else {
    $('#proc-current').innerHTML='<div class="fn">'+(j.status==='completed'?'All files processed.':'Waiting&hellip;')+'</div>';
  }
  $('#proc-bar').style.width=(st.pct||0)+'%';
  $('#proc-metrics').innerHTML=
     metric(proc+'/'+(j.total||0),'files done')
    +metric((j.chunks||0).toLocaleString(),'chunks')
    +metric(fmtETA(st.eta_s),'est. remaining')
    +metric((st.files_per_min||0)+'/min'+(j.failed?(' · '+j.failed+' failed'):''),'throughput');
  // recent files (newest first) with durations
  const rows=(j.results||[]).slice(-40).reverse();
  $('#proc-log').innerHTML=rows.length?rows.map(r=>
    '<div class="row"><span class="nm">'+esc(r.name)+'</span>'
    +'<span class="sc"><span class="badge '+(r.status==='error'?'error':(r.status||'ok'))+'">'
    +esc(r.status||'ok')+'</span> '+(r.chunks||0)+'ch · '+(r.secs!=null?r.secs+'s':'')+'</span></div>'
  ).join(''):'<div class="row"><span class="muted">No files completed yet.</span></div>';
}
function metric(v,l){return '<div class="m"><b>'+esc(String(v))+'</b><span>'+esc(l)+'</span></div>';}
pollActive();
async function saveKey(){
  const key=$('#keyin').value.trim(), model=$('#modelin').value.trim();
  const r=await fetch('/api/key',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key,model:model||null})}).then(r=>r.json());
  closeKey(); toast('Connected: '+(r.llm||'offline')); refresh();
}

const EXAMPLES=['How many firearms were manufactured in the United States in 2023?',
  'What are privately made firearms (ghost guns)?',
  'How does the National Tracing Center trace a firearm?',
  'What common themes recur across the incident reports?'];
const exwrap=$('#examples');
EXAMPLES.forEach(t=>{const d=document.createElement('div');d.className='ex';d.textContent=t;
  d.onclick=()=>{$('#q').value=t;ask();};exwrap.appendChild(d);});

function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmt(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');}
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')ask();});

async function ask(){
  const q=$('#q').value.trim(); if(!q)return;
  $('#emptyhint')&&($('#emptyhint').style.display='none');
  $('#q').value=''; $('#send').disabled=true;
  addMsg('user',esc(q));
  const tid=addTyping();
  try{
    const res=await fetch('/query',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q,trace:true})}).then(r=>r.json());
    document.getElementById(tid).remove(); renderAnswer(res);
  }catch(e){document.getElementById(tid).remove();addMsg('bot','<span style="color:var(--err)">Error: '+esc(''+e)+'</span>');}
  $('#send').disabled=false; $('#q').focus();
}
function addMsg(role,html){
  const m=document.createElement('div');m.className='msg '+role;
  m.innerHTML='<div class="av">'+(role==='user'?'\u{1F9D1}':'◆')+'</div><div class="bubble">'+html+'</div>';
  $('#msgs').appendChild(m); $('#msgs').scrollTop=1e9; return m;
}
function addTyping(){const id='t'+Date.now();const m=document.createElement('div');
  m.className='msg bot';m.id=id;
  m.innerHTML='<div class="av">◆</div><div class="bubble"><span class="spin" style="border-color:#c7d2fe;border-top-color:#4f46e5"></span> thinking&hellip;</div>';
  $('#msgs').appendChild(m);$('#msgs').scrollTop=1e9;return id;}

function renderAnswer(res){
  const cites=res.citations||[]; const mode=res.mode||'local';
  const conf=res.confidence!=null?Math.round(res.confidence*100)+'%':'';
  const tm=((res.trace||{}).timings_ms||{}).total;
  let html='<div class="ans">'+fmt(res.answer||'')+'</div><div class="meta">';
  html+='<span class="chip mode-'+mode+'">'+mode+' mode</span>';
  if(res.intent)html+='<span class="chip">intent: '+esc(res.intent)+'</span>';
  if(conf)html+='<span class="chip">confidence '+conf+'</span>';
  if(tm)html+='<span class="chip">'+Math.round(tm)+' ms</span>';
  html+='<span class="chip">'+cites.length+' sources</span>';
  if(res.incomplete){html+='<span class="chip" style="background:#fef3c7;color:#92400e" title="'+
    esc(res.notes||'')+'">&#9888; evidence may be incomplete</span>';}
  const wr=res.web_research||{};
  if(wr.triggered){
    const added=wr.added||0;
    html+='<span class="chip" style="background:#ecfeff;color:#0e7490" title="'+
      esc(wr.reason||'')+'">&#127760; web research'+(added?': +'+added+' new':' (no new)')+'</span>';
  }
  html+='</div>';
  const paths=(res.graph_paths||[]);
  if(paths.length){html+='<div class="paths">&#128279; '+paths.slice(0,4).map(p=>'<code>'+esc(p)+'</code>').join(' &nbsp; ')+'</div>';}
  if(cites.length){
    const sid='s'+Date.now()+Math.floor(Math.random()*1e4);
    html+='<button class="srcbtn" onclick="document.getElementById(\''+sid+'\').classList.toggle(\'open\')">&#9656; Show sources &amp; resources</button>';
    html+='<div class="srcs" id="'+sid+'">';
    cites.forEach(c=>{
      const name=c.source||(c.sources&&c.sources.join(', '))||(c.members&&'Community: '+c.members.slice(0,4).join(', '))||'source';
      const loc=c.page?('p.'+c.page):(c.corpus||''); const ct=c.content_type?(' · '+c.content_type):'';
      const cid=c.chunk_id||(c.chunk_ids&&c.chunk_ids[0])||'';
      html+='<div class="src" onclick="preview(\''+cid+'\')"><div class="t">['+(c.ref||'')+'] '+esc(name)+'</div>'
           +'<div class="d">'+esc(loc)+ct+(cid?' · click to preview':'')+'</div></div>';
    });
    html+='</div>';
  }
  addMsg('bot',html);
}
async function preview(cid){
  if(!cid)return;
  const r=await fetch('/api/chunk',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({chunk_id:cid})}).then(r=>r.json());
  if(r.error){toast('Preview unavailable');return;}
  addMsg('bot','<div style="color:var(--accent);font-size:12px;font-weight:600">SOURCE &middot; '+esc(r.source_name||'')+(r.page_number?(' &middot; p.'+r.page_number):'')+'</div><div class="ans" style="margin-top:6px;font-size:13px;color:#475569">'+esc((r.text||'').slice(0,800))+'&hellip;</div>');
}

const drop=$('#drop'),filePick=$('#filePick'),folderPick=$('#folderPick');
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over');}));
drop.addEventListener('drop',ev=>stage([...ev.dataTransfer.files]));
drop.onclick=()=>filePick.click();
filePick.onchange=e=>stage([...e.target.files]);
folderPick.onchange=e=>stage([...e.target.files]);
function stage(files){
  files=files.filter(f=>/\.(pdf|txt|md|markdown|html?|png|jpe?g|tiff?)$/i.test(f.name));
  pending=files; const b=$('#upbtn');
  b.textContent='Ingest '+files.length+' file'+(files.length!=1?'s':''); b.disabled=!files.length;
  if(files.length)toast(files.length+' file(s) ready');
}
function readFile(f){return new Promise(res=>{const r=new FileReader();r.onload=()=>res({name:f.name,content_b64:r.result});r.readAsDataURL(f);});}
const BATCH=6;   // files per HTTP request — keeps each body small & reliable
async function doUpload(){
  if(!pending.length)return;
  const mode=$('#upmode').value, corpus=$('#corpus').value;
  const b=$('#upbtn');b.disabled=true;b.innerHTML='<span class="spin"></span> '+(mode==='async'?'Uploading&hellip;':'Ingesting&hellip;');
  $('#prog').classList.add('show');$('#bar').style.width='4%';
  const tbody=$('#uptbody');tbody.innerHTML='';$('#uptbl').style.display='table';
  const all=pending.slice(); pending=[];
  if(mode==='async') return doUploadAsync(all,corpus,b);
  // ---- SYNC: ingest each batch inline, show results as they land ----
  let done=0,total=0;
  for(let i=0;i<all.length;i+=BATCH){
    const slice=all.slice(i,i+BATCH);
    const files=await Promise.all(slice.map(readFile));
    try{
      const r=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({files,corpus,mode:'sync'})}).then(r=>r.json());
      (r.results||[]).forEach(x=>{total+=x.chunks||0; addUpRow(tbody,x);});
    }catch(e){toast('Upload error on a batch');}
    done+=slice.length;$('#bar').style.width=Math.round(done/all.length*100)+'%';
  }
  b.innerHTML='Ingest 0 files';b.disabled=true;
  toast('Indexed '+total+' chunks from '+done+' files');refresh();
  setTimeout(()=>$('#prog').classList.remove('show'),800);
}
async function doUploadAsync(all,corpus,b){
  // Stage every file to a durable job in small batches, then poll progress.
  let jid=null, staged=0;
  for(let i=0;i<all.length;i+=BATCH){
    const slice=all.slice(i,i+BATCH);
    const files=await Promise.all(slice.map(readFile));
    const final=(i+BATCH>=all.length);
    try{
      const r=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({files,corpus,mode:'async',job_id:jid,final})}).then(r=>r.json());
      jid=r.job_id; staged+=r.staged||0;
      $('#bar').style.width=Math.round((i+slice.length)/all.length*30)+'%';   // upload = first 30%
    }catch(e){toast('Upload error on a batch');}
  }
  b.innerHTML='Ingest 0 files';b.disabled=true;
  if(!jid){setTimeout(()=>$('#prog').classList.remove('show'),800);return;}
  toast('Queued '+staged+' files — ingesting in background');
  pollJob(jid);
}
function addUpRow(tbody,x){
  tbody.insertAdjacentHTML('beforeend','<tr><td>'+esc(x.name)+'</td><td><span class="chip">'+esc(x.type||'?')+'</span></td>'
    +'<td><span class="badge '+(x.status==='error'?'error':(x.status||'ok'))+'">'+esc(x.status||'ok')+'</span></td>'
    +'<td style="text-align:right">'+(x.chunks||0)+'</td></tr>');
}
async function pollJob(jid){
  const tbody=$('#uptbody');let shown=0;
  const tick=async()=>{
    let j;try{j=await fetch('/api/jobs/'+jid).then(r=>r.json());}catch(e){return setTimeout(tick,2000);}
    const proc=(j.done||0)+(j.failed||0), tot=j.total||0;
    const pct=tot?30+Math.round(proc/tot*70):30;   // ingest = remaining 70%
    $('#bar').style.width=pct+'%';
    (j.results||[]).slice(shown).forEach(x=>addUpRow(tbody,x)); shown=(j.results||[]).length;
    $('#upbtn').innerHTML='Job '+proc+'/'+tot+' &middot; '+(j.chunks||0)+' chunks';
    if(j.status==='completed'){
      toast('Done: '+proc+'/'+tot+' files, '+(j.chunks||0)+' chunks'+(j.failed?(', '+j.failed+' failed'):''));
      $('#upbtn').innerHTML='Ingest 0 files';refresh();
      setTimeout(()=>$('#prog').classList.remove('show'),1000);return;
    }
    setTimeout(tick,1500);
  };
  tick();
}
async function setExtraction(){
  const mode=$('#upextract').value;
  try{await fetch('/api/config/extraction',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode})});toast('Entity extraction: '+mode);}
  catch(e){toast('Could not set extraction mode');}
}
async function buildCommunities(btn){
  btn.disabled=true;const o=btn.innerHTML;btn.innerHTML='<span class="spin"></span> Building&hellip;';
  try{const r=await fetch('/api/communities/build',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
    toast('Built '+(r.communities||0)+' communities');refresh();}
  catch(e){toast('Build failed');}
  btn.disabled=false;btn.innerHTML=o;
}
async function verifyGraph(btn){
  if(!confirm('LLM cross-verify the graph and remove nodes that are not real '+
    'entities (dates, headers, generic words, noise)? This prunes them and their '+
    'edges.'))return;
  btn.disabled=true;const o=btn.innerHTML;btn.innerHTML='<span class="spin"></span> Cleaning&hellip;';
  try{const r=await fetch('/api/graph/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
    toast('Cleaned: '+(r.rule_dropped||0)+' rule + '+(r.llm_dropped||0)+' LLM nodes removed ('+
      (r.edges_removed||0)+' edges) · '+(r.nodes_after||0)+' kept');
    if(typeof loadGraph==='function')loadGraph(); else refresh();}
  catch(e){toast('Verify failed');}
  btn.disabled=false;btn.innerHTML=o;
}

let G=null,sim=null,svg=null,node=null,link=null,label=null,graphSelected=null;
const PALETTE=['#6366f1','#ec4899','#f59e0b','#10b981','#3b82f6','#8b5cf6','#ef4444','#14b8a6','#a855f7','#0ea5e9','#f97316','#84cc16'];
// Richer entity-type ontology palette (graph UI). Unknown types fall back to grey.
const TYPECOLOR={
  manufacturer:'#6366f1', organization:'#14b8a6', agency:'#0891b2',
  person:'#f43f5e', seller:'#ec4899', buyer:'#8b5cf6',
  location:'#10b981', firearm_type:'#f59e0b', ammunition:'#d97706',
  incident_type:'#ef4444', case:'#0ea5e9', statute:'#64748b',
  event:'#a855f7', date:'#22c55e', vehicle:'#7c3a26', money:'#16a34a',
  entity:'#94a3b8'};
const TYPELABEL={manufacturer:'Manufacturer',organization:'Organization',agency:'Agency',
  person:'Person',seller:'Seller',buyer:'Buyer',location:'Location',firearm_type:'Firearm type',
  ammunition:'Ammunition',incident_type:'Incident type',case:'Case',statute:'Statute',
  event:'Event',date:'Date',vehicle:'Vehicle',money:'Money',entity:'Entity'};
async function loadGraph(){
  const c=$('#gcanvas');[...c.querySelectorAll('svg')].forEach(s=>s.remove());
  $('#ginfo').innerHTML='<div style="color:var(--muted);text-align:center;margin-top:40px"><span class="spin" style="border-color:#c7d2fe;border-top-color:#4f46e5"></span><br>Loading&hellip;</div>';
  G=await fetch('/graph/export').then(r=>r.json());
  if(!G.nodes||!G.nodes.length){$('#ginfo').innerHTML='<div style="color:var(--muted);text-align:center;margin-top:40px">Graph is empty &mdash; ingest documents first.</div>';return;}
  const types=[...new Set(G.nodes.map(n=>n.type))];
  $('#gtype').innerHTML='<option value="">all types</option>'+types.map(t=>'<option>'+t+'</option>').join('');
  drawGraph();
  const meta=G.community_meta||{};
  const comms=[...new Set(G.nodes.map(n=>n.community))].filter(c=>c>=0).sort((a,b)=>a-b).slice(0,10);
  const cname=c=>((meta[c]&&meta[c].name)?meta[c].name:('Community '+c));
  // Types actually present in the graph, for the type legend.
  const present=[...new Set(G.nodes.map(n=>n.type))].sort();
  let lg='<b class="lgh">COMMUNITIES</b>'+
    comms.map(c=>'<div class="li" title="'+esc((meta[c]||{}).summary||'')+'">'+
      '<span class="sw" style="background:'+PALETTE[c%PALETTE.length]+'"></span>'+
      '<span class="lgt">'+esc(cname(c))+'</span></div>').join('')+
    '<div class="li"><span class="sw" style="background:#cbd5e1"></span><span class="lgt">ungrouped</span></div>';
  lg+='<b class="lgh" style="margin-top:10px">ENTITY TYPES</b>'+
    present.map(t=>'<div class="li"><span class="sw" style="background:'+(TYPECOLOR[t]||'#94a3b8')+'"></span>'+
      '<span class="lgt">'+esc(TYPELABEL[t]||t)+'</span></div>').join('');
  $('#legend').innerHTML=lg;
  $('#ginfo').innerHTML='<div style="color:var(--muted);text-align:center;margin-top:40px"><div style="font-size:40px">&#128376;</div>'+G.nodes.length+' entities, '+G.edges.length+' connections.<br>Click a node to inspect.</div>';
}
function colorOf(n){return n.community>=0?PALETTE[n.community%PALETTE.length]:(TYPECOLOR[n.type]||'#cbd5e1');}
let gZoom=null, gRoot=null;
function drawGraph(){
  const c=$('#gcanvas'),W=c.clientWidth,H=c.clientHeight;
  svg=d3.select(c).append('svg').attr('viewBox',[0,0,W,H]);
  const g=svg.append('g'); gRoot=g;
  gZoom=d3.zoom().scaleExtent([.1,5]).on('zoom',ev=>g.attr('transform',ev.transform));
  svg.call(gZoom);
  svg.on('click',()=>clearHighlight());   // click empty space to reset highlight
  const nodes=G.nodes.map(d=>({...d})),id=new Set(nodes.map(n=>n.id));
  const links=G.edges.filter(e=>id.has(e.source)&&id.has(e.target)).map(d=>({...d}));
  const rOf=d=>5+Math.sqrt(d.degree)*1.8;          // node radius

  // Build the simulation but DO NOT run it live (that causes the endless drift).
  // We tick it headless to a settled state, render once, then leave it stopped.
  sim=d3.forceSimulation(nodes)
    .force('link',d3.forceLink(links).id(d=>d.id).distance(d=>d.typed?70:110).strength(.18))
    .force('charge',d3.forceManyBody().strength(-240).distanceMax(480))
    .force('x',d3.forceX(W/2).strength(.06))
    .force('y',d3.forceY(H/2).strength(.06))
    .force('collide',d3.forceCollide(d=>rOf(d)+16).iterations(2))
    .velocityDecay(.45)
    .stop();
  // Headless warm-up: scale ticks to graph size so it always reaches equilibrium.
  const ticks=Math.min(400,Math.max(120,Math.round(nodes.length/4)));
  for(let i=0;i<ticks;i++) sim.tick();

  link=g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke-width',d=>d.typed?1.8:.7).attr('stroke',d=>d.typed?'#a5b4fc':'#cbd5e1').attr('stroke-opacity',.55);
  node=g.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r',rOf).attr('fill',colorOf)
    .attr('stroke','#fff').attr('stroke-width',1.4).style('cursor','pointer')
    .on('click',(e,d)=>{e.stopPropagation();showNode(d);highlightNode(d);})
    .call(d3.drag()
      .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(.2).restart();d.fx=d.x;d.fy=d.y;})
      .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y;})
      .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=d.x;d.fy=d.y;}));  // pin where dropped
  node.append('title').text(d=>d.name+' ('+d.type+') · '+d.degree+' links');
  const labMin=nodes.length>900?16:(nodes.length>400?9:4);
  label=g.append('g').selectAll('text').data(nodes.filter(n=>n.degree>=labMin)).join('text')
    .text(d=>d.name.length>26?d.name.slice(0,24)+'…':d.name)
    .attr('font-size',10).attr('font-weight',600).attr('text-anchor','middle')
    .attr('fill','#1e293b').attr('stroke','#ffffff').attr('stroke-width',3.2)
    .style('paint-order','stroke').style('pointer-events','none');

  function render(){
    link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
    node.attr('cx',d=>d.x).attr('cy',d=>d.y);
    label.attr('x',d=>d.x).attr('y',d=>d.y+rOf(d)+12);   // aligned beneath node
  }
  render();                       // paint the settled layout once (static)
  // Only redraw while the user is actively dragging; otherwise stay still.
  sim.on('tick',render);
  fitToView(nodes,W,H);           // zoom-to-fit so the whole graph is framed
}

function fitToView(nodes,W,H){
  if(!nodes.length||!gZoom)return;
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  nodes.forEach(n=>{x0=Math.min(x0,n.x);y0=Math.min(y0,n.y);x1=Math.max(x1,n.x);y1=Math.max(y1,n.y);});
  const dw=x1-x0||1,dh=y1-y0||1,pad=50;
  // Clamp zoom so the graph is always legible: never a tiny dot (min .25),
  // never absurdly large (max 1.6). Below fit-scale it overflows but pans.
  const k=Math.max(.25,Math.min(1.6,0.92*Math.min((W-pad)/dw,(H-pad)/dh)));
  const tx=W/2-k*(x0+x1)/2, ty=H/2-k*(y0+y1)/2;
  svg.transition().duration(500).call(gZoom.transform,
    d3.zoomIdentity.translate(tx,ty).scale(k));
}
function highlightNode(d){
  // Highlight ONLY the clicked node + its directly-connected neighbours and the
  // edges between them; dim everything else.
  if(!node)return;
  const conn=new Set([d.id]);
  G.edges.forEach(e=>{const a=(e.source.id||e.source),b=(e.target.id||e.target);
    if(a===d.id)conn.add(b); if(b===d.id)conn.add(a);});
  node.attr('opacity',n=>conn.has(n.id)?1:.07)
      .attr('stroke',n=>n.id===d.id?'#4f46e5':'#fff')
      .attr('stroke-width',n=>n.id===d.id?3.5:(conn.has(n.id)?2:1.4));
  if(link)link.attr('stroke-opacity',e=>{const a=(e.source.id||e.source),b=(e.target.id||e.target);
    return (a===d.id||b===d.id)?.95:.03;})
    .attr('stroke',e=>{const a=(e.source.id||e.source),b=(e.target.id||e.target);
    return (a===d.id||b===d.id)?(e.typed?'#6366f1':'#94a3b8'):(e.typed?'#a5b4fc':'#cbd5e1');});
  if(label)label.attr('opacity',n=>conn.has(n.id)?1:.04);
  graphSelected=d.id;
}
function clearHighlight(){
  if(!node)return;
  node.attr('opacity',1).attr('stroke','#fff').attr('stroke-width',1.4);
  if(link)link.attr('stroke-opacity',.55)
    .attr('stroke',d=>d.typed?'#a5b4fc':'#cbd5e1');
  if(label)label.attr('opacity',1);
  graphSelected=null;
}
function showNode(d){
  const nbrs=G.edges.filter(e=>{const a=(e.source.id||e.source),b=(e.target.id||e.target);return a===d.id||b===d.id;});
  const rel=nbrs.slice(0,18).map(e=>{const a=(e.source.id||e.source),b=(e.target.id||e.target);
    const other=(a===d.id)?b:a;const on=(G.nodes.find(n=>n.id===other)||{}).name||other;
    return '<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--line)">'+(e.typed?'<b style="color:var(--accent)">'+esc(e.relation)+'</b> ':'· ')+esc(on)+'</div>';}).join('');
  const cmeta=(G.community_meta||{})[d.community]||null;
  const cnm=cmeta?cmeta.name:('community '+d.community);
  let h='<div class="ty">'+esc(d.type)+'</div><h3>'+esc(d.name)+'</h3>';
  h+='<div style="color:var(--muted);font-size:12px">'+d.degree+' connections'+(d.community>=0?(' · '+esc(cnm)):'')+'</div>';
  const cbrief=cmeta?cmeta.summary:(G.communities&&G.communities[d.community]);
  if(cbrief){h+='<div class="k">'+esc(cnm)+'</div><div style="font-size:12.5px;color:#475569">'+esc(cbrief)+'</div>';}
  h+='<div class="k">Connections</div>'+(rel||'<span style="color:var(--muted)">none</span>');
  h+='<div class="k">Source documents</div><div id="nodesrc"><span style="color:var(--muted);font-size:12px">loading&hellip;</span></div>';
  $('#ginfo').innerHTML=h;
  const cids=(d.chunk_ids||[]).slice(0,4);
  Promise.all(cids.map(id=>fetch('/api/chunk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chunk_id:id})}).then(r=>r.json()).catch(()=>null)))
    .then(rs=>{const names=[...new Set(rs.filter(Boolean).map(r=>r.source_name).filter(Boolean))];
      $('#nodesrc').innerHTML=names.length?names.map(n=>'<div class="src" style="cursor:default">&#128196; '+esc(n)+'</div>').join(''):'<span style="color:var(--muted);font-size:12px">&mdash;</span>';});
}
function filterGraph(){
  if(!node)return; const q=$('#gsearch').value.toLowerCase(),ty=$('#gtype').value;
  const ok=d=>(!q||d.name.toLowerCase().includes(q))&&(!ty||d.type===ty);
  node.attr('opacity',d=>ok(d)?1:.08);
  if(label)label.attr('opacity',d=>ok(d)?1:.05);
}

/* ---------------- AWS Native tab ---------------- */
function awsVsFields(){const q=$('#aws-vs').value==='qdrant';
  $('#aws-vs-ep-l').firstChild.textContent=q?'Qdrant URL':'OpenSearch host';
  $('#aws-vs-ep').placeholder=q?'http://host:6333':'https://search-xxx.es.amazonaws.com';
  $('#aws-vs-key-l').style.display=q?'flex':'none';}
function awsGsFields(){const neo=$('#aws-gs').value==='neo4j';
  $('#aws-gs-ep-l').style.display=neo?'none':'flex';
  $('#aws-gs-port-l').style.display=neo?'none':'flex';
  $('#aws-gs-uri-l').style.display=neo?'flex':'none';}
function awsForm(){
  return {region:$('#aws-region').value.trim(),
    llm:{model:$('#aws-llm').value.trim()},
    vision:{model:$('#aws-vision').value.trim()},
    embeddings:{model:$('#aws-emb').value.trim(),dim:$('#aws-embdim').value.trim()},
    reranker:{enabled:$('#aws-rr').value.trim()!=='',model:$('#aws-rr').value.trim()},
    ocr:{enabled:$('#aws-ocr').value==='1'},
    vector_store:{provider:$('#aws-vs').value,url:$('#aws-vs-ep').value.trim(),
      host:$('#aws-vs-ep').value.trim(),api_key:$('#aws-vs-key').value.trim(),
      prefix:$('#aws-vs-prefix').value.trim(),dim:$('#aws-embdim').value.trim()},
    graph_store:{provider:$('#aws-gs').value,endpoint:$('#aws-gs-ep').value.trim(),
      port:$('#aws-gs-port').value.trim(),uri:$('#aws-gs-uri').value.trim()},
    blob_store:{bucket:$('#aws-s3').value.trim(),prefix:$('#aws-s3prefix').value.trim()},
    parser:{provider:$('#aws-parser').value},
    bda:{project_arn:$('#aws-bda-arn').value.trim(),bucket:$('#aws-bda-bucket').value.trim()},
    guardrails:{enabled:$('#aws-gr-en').value==='1',guardrail_id:$('#aws-gr-id').value.trim(),
      guardrail_version:$('#aws-gr-ver').value.trim(),
      automated_reasoning_policy:$('#aws-ar-arn').value.trim()}};
}
async function awsRagEval(btn){
  const body={role_arn:$('#aws-ev-role').value.trim(),dataset_s3:$('#aws-ev-data').value.trim(),
    output_s3:$('#aws-ev-out').value.trim(),region:$('#aws-region').value.trim()||'us-east-1'};
  if(!body.role_arn||!body.dataset_s3||!body.output_s3){toast('Fill role ARN, dataset and output S3');return;}
  const o=btn.innerHTML;btn.disabled=true;btn.innerHTML='Submitting&hellip;';
  try{const r=await fetch('/api/aws/rag-eval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    $('#aws-ev-res').innerHTML=r.ok?('&#9989; Submitted: '+esc(r.jobArn||'job created')):('&#10060; '+esc(r.error||'failed'));
  }catch(e){$('#aws-ev-res').textContent='Submit failed';}
  btn.disabled=false;btn.innerHTML=o;
}
function awsRenderWiring(w){
  const aws=/Bedrock|Qdrant|OpenSearch|Neptune|Neo4j|Textract|S3/;
  const keys=['profile','llm','embedder','vision','reranker','ocr','vector_store','graph_store','blob_store'];
  $('#aws-wiring').innerHTML='<span class="wchip">Active profile: <b>'+(w.profile||'?')+'</b></span>'+
    keys.slice(1).map(k=>'<span class="wchip'+(aws.test(w[k])?' aws':'')+'">'+k+': <b>'+w[k]+'</b></span>').join('');
}
async function awsStatus(){
  try{const s=await fetch('/api/aws/status').then(r=>r.json());
    awsRenderWiring(s.wiring||{});
    const c=s.credentials||{};
    $('#aws-credstate').textContent=c.aws_access_key_id?('Active region '+(c.aws_region||'?')):'no AWS credentials set';
  }catch(e){}
}
async function awsSaveCreds(){
  const body={region:$('#aws-region').value.trim(),access_key_id:$('#aws-akid').value.trim(),
    secret_access_key:$('#aws-secret').value,session_token:$('#aws-token').value,
    neo4j_user:$('#aws-n4user').value.trim(),neo4j_password:$('#aws-n4pass').value,
    neptune_endpoint:$('#aws-gs-ep').value.trim()};
  const r=await fetch('/api/aws/credentials',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(r=>r.json());
  toast('Credentials stored in memory');awsStatus();
}
async function awsValidate(){
  $('#aws-results').innerHTML='<p class="muted">Probing components&hellip;</p>';
  try{
    const r=await fetch('/api/aws/validate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(awsForm())}).then(r=>r.json());
    const rows=(r.results||[]).map(x=>'<div class="awsrow '+(x.ok?'ok':'bad')+'">'+
      '<span class="pill">'+(x.ok?'OK':'FAIL')+'</span>'+
      '<span class="cmp">'+x.component+'</span>'+
      '<span class="dt">'+x.detail+'</span>'+
      '<span class="ms">'+x.provider+' &middot; '+x.ms+'ms</span></div>').join('');
    $('#aws-results').innerHTML='<p class="muted" style="margin-top:14px">Region '+r.region+
      ' &middot; '+r.summary.ok+'/'+r.summary.total+' components reachable</p>'+rows;
  }catch(e){$('#aws-results').innerHTML='<p class="muted">Validation error: '+e+'</p>';}
}
async function awsApply(){
  try{const r=await fetch('/api/aws/apply',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(awsForm())}).then(r=>r.json());
    awsRenderWiring(r.wiring||{});toast('Engine switched to AWS-native wiring');refresh();
  }catch(e){toast('Apply failed: '+e);}
}
async function awsSmoke(){
  $('#aws-smoke').innerHTML='<p class="muted">Running ingest &rarr; index &rarr; query&hellip;</p>';
  try{const r=await fetch('/api/aws/smoke',{method:'POST'}).then(r=>r.json());
    const t=r.timings_ms||{};
    const tline=Object.keys(t).map(k=>k+' '+Math.round(t[k])+'ms').join(' &middot; ');
    $('#aws-smoke').innerHTML='<div class="awsrow '+(r.ok?'ok':'bad')+'" style="align-items:flex-start">'+
      '<span class="pill">'+(r.ok?'OK':'FAIL')+'</span><div style="flex:1">'+
      '<div><b>Q:</b> '+r.question+'</div>'+
      '<div style="margin-top:5px"><b>A:</b> '+(r.answer||r.error||'(no answer)')+'</div>'+
      '<div class="ms" style="margin-top:6px">'+(r.chunks_indexed||0)+' chunks &middot; '+
      (r.citations||[]).length+' citations &middot; '+tline+'</div></div></div>';
    awsRenderWiring(r.wiring||{});
  }catch(e){$('#aws-smoke').innerHTML='<p class="muted">Smoke test error: '+e+'</p>';}
}
async function awsRevert(){
  try{const r=await fetch('/api/aws/revert',{method:'POST'}).then(r=>r.json());
    awsRenderWiring(r.wiring||{});toast('Reverted to local engine');refresh();
  }catch(e){toast('Revert failed: '+e);}
}
// ---- Configuration: compose your RAG from building blocks ----
let CFG=null;
async function loadConfig(){
  try{CFG=await fetch('/api/config/blocks').then(r=>r.json());}catch(e){return;}
  $('#cfg-preset').innerHTML=(CFG.profiles||[]).map(p=>'<option'+(p===CFG.profile?' selected':'')+'>'+p+'</option>').join('');
  renderBlocks(); cfgWiring(CFG.wiring||{});
}
function renderBlocks(){
  const rows=(CFG.blocks||[]).map(b=>{
    const opts=b.options.map(o=>'<option'+(o===b.current?' selected':'')+'>'+o+'</option>').join('');
    const tag=b.runtime?'<span style="font-size:11px;color:#16a34a;font-weight:700">runtime</span>'
                       :'<span style="font-size:11px;color:#b45309;font-weight:700">needs re-ingest</span>';
    return '<tr><td style="font-weight:600">'+esc(b.label)+'</td>'+
      '<td><select id="cfgb-'+b.key+'" style="padding:7px 9px;border:1px solid var(--line);border-radius:8px;min-width:170px">'+opts+'</select></td>'+
      '<td>'+tag+'</td><td class="s">'+esc(b.cost)+'</td></tr>';
  }).join('');
  $('#cfg-blocks').innerHTML='<table class="kbtable"><thead><tr><th>Block</th><th>Provider</th><th>Apply</th><th>Cost</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
async function loadPreset(){
  const p=$('#cfg-preset').value;
  // load preset = apply with the chosen profile and no overrides (resets blocks to that profile's defaults)
  applyConfig(null,{profile:p,blocks:{}});
}
function cfgWiring(w){
  $('#cfg-wiring').innerHTML=Object.entries(w).map(([k,v])=>
    '<div class="row"><span>'+esc(k)+'</span><b>'+esc(v)+'</b></div>').join('');
}
async function applyConfig(btn,override){
  const body=override||{profile:$('#cfg-preset').value,blocks:Object.fromEntries(
    (CFG.blocks||[]).map(b=>[b.key,$('#cfgb-'+b.key).value]))};
  const o=btn?btn.innerHTML:''; if(btn){btn.disabled=true;btn.innerHTML='Applying&hellip;';}
  try{const r=await fetch('/api/config/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    cfgWiring(r.wiring||{});
    let msg='Applied ('+r.profile+').';
    if((r.needs_reingest||[]).length) msg+=' &#9888; '+r.needs_reingest.join(', ')+' changed — re-ingest or import to repopulate.';
    $('#cfg-result').innerHTML='<div style="font-size:13px;color:#334155">'+msg+'</div>';
    toast('Configuration applied'); loadConfig(); refresh();
  }catch(e){toast('Apply failed');}
  if(btn){btn.disabled=false;btn.innerHTML=o;}
}
// ---- AWS provision / teardown control plane ----
function _provBody(action){return JSON.stringify({
  action, project:($('#aws-proj').value||'atf-graphrag').trim(),
  region:($('#aws-proj-region').value||'us-east-1').trim()});}
function _provOut(html){$('#aws-prov').innerHTML=html;}
async function awsPlan(action){
  _provOut('<span class="muted">Planning&hellip;</span>');
  try{const r=await fetch('/api/aws/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:_provBody(action)}).then(r=>r.json());
    const rows=(r.steps||[]).map(s=>'<tr><td>'+esc(s.label)+'</td><td>'+s.action+'</td><td class="num">$'+s.cost_month+'/mo</td></tr>').join('');
    _provOut('<div class="muted" style="margin-bottom:6px">account '+(r.account_id||'?')+' · region '+esc(r.region)+' · boto3 '+(r.boto3?'available':'MISSING')+'</div>'+
      '<table class="kbtable"><thead><tr><th>Resource</th><th>Action</th><th class="num">Est. cost</th></tr></thead><tbody>'+rows+'</tbody></table>'+
      '<div style="margin-top:6px"><b>Est. running cost: $'+(r.est_cost_month||0)+'/month</b> if left on — that\'s why you teardown when done.</div>');
  }catch(e){_provOut('<span class="bad">Plan failed: '+esc(''+e)+'</span>');}
}
async function awsInventory(){
  _provOut('<span class="muted">Scanning account&hellip;</span>');
  try{const r=await fetch('/api/aws/inventory',{method:'POST',headers:{'Content-Type':'application/json'},body:_provBody('inventory')}).then(r=>r.json());
    const rows=(r.components||[]).map(c=>'<tr><td>'+esc(c.component)+'</td><td>'+(c.exists===true?'<span style=color:#16a34a>live</span>':c.exists===false?'<span class=muted>absent</span>':'<span class=bad>?</span>')+'</td><td class="num">$'+(c.cost_month||0)+'/mo</td></tr>').join('');
    _provOut('<table class="kbtable"><thead><tr><th>Component</th><th>State</th><th class="num">Cost</th></tr></thead><tbody>'+rows+'</tbody></table>'+
      '<div style="margin-top:6px"><b>'+(r.n_live||0)+' live · running cost ~$'+(r.running_cost_month||0)+'/month</b></div>');
  }catch(e){_provOut('<span class="bad">Inventory failed: '+esc(''+e)+'</span>');}
}
async function awsProvision(){
  if(!confirm('Provision the AWS-native stack (S3, DynamoDB, SSM, Guardrail, OpenSearch Serverless, Neptune Analytics)? '+
    'Neptune + OpenSearch cost ~$700/month while running — remember to tear down when done.'))return;
  _provOut('<span class="muted">Provisioning (Neptune/OpenSearch are async, ~minutes)&hellip;</span>');
  try{const r=await fetch('/api/aws/provision',{method:'POST',headers:{'Content-Type':'application/json'},body:_provBody('provision')}).then(r=>r.json());
    _provOut(awsResultTable(r.results)+'<div style="margin-top:6px">'+(r.ok?'<b style=color:#16a34a>Provision requested.</b>':'<b class=bad>Some steps failed — see above.</b>')+'</div>');toast('Provision '+(r.ok?'OK':'had errors'));
  }catch(e){_provOut('<span class="bad">Provision failed: '+esc(''+e)+'</span>');}
}
async function awsTeardown(){
  if(!confirm('DELETE every AWS resource tagged Project=atf-graphrag — S3 buckets (and contents), DynamoDB, SSM, Guardrail, OpenSearch Serverless collection, and the Neptune Analytics graph? This stops all charges and CANNOT be undone.'))return;
  if(!confirm('Final confirmation: permanently delete the AWS-native stack now?'))return;
  _provOut('<span class="muted">Tearing down (reverse order)&hellip;</span>');
  try{const r=await fetch('/api/aws/teardown',{method:'POST',headers:{'Content-Type':'application/json'},body:_provBody('teardown')}).then(r=>r.json());
    _provOut(awsResultTable(r.results)+'<div style="margin-top:6px">'+(r.ok?'<b style=color:#16a34a>Teardown complete — charges stopped.</b>':'<b class=bad>Some deletions failed — check the AWS console.</b>')+'</div>');toast('Teardown '+(r.ok?'complete':'had errors'));
  }catch(e){_provOut('<span class="bad">Teardown failed: '+esc(''+e)+'</span>');}
}
function awsResultTable(results){
  const rows=(results||[]).map(x=>'<tr><td>'+esc(x.component)+'</td><td>'+(x.ok?'<span style=color:#16a34a>ok</span>':'<span class=bad>error</span>')+'</td><td>'+esc(x.detail||x.error||'')+'</td></tr>').join('');
  return '<table class="kbtable"><thead><tr><th>Component</th><th>Result</th><th>Detail</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
</script>
</body>
</html>'''
