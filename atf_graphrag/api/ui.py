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
        <button class="btn" id="upbtn" onclick="doUpload()" disabled>Ingest 0 files</button>
        <span style="margin-left:auto"><button class="btn ghost" onclick="buildCommunities(this)">&#8635; Rebuild communities</button></span>
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
    graph:['Graph','Explore entities, communities and connections']};
  $('#t-title').textContent=titles[v][0]; $('#t-sub').textContent=titles[v][1];
  if(v==='kb') loadKB();
});

async function refresh(){
  try{
    const s=await fetch('/api/status').then(r=>r.json());
    $('#c-prov').textContent=(s.llm||'').split(':')[0]||'offline';
    $('#c-model').textContent=(s.llm||'').split(':').slice(1).join(':')||'—';
    $('#c-key').innerHTML='<span class="dot '+(s.key_set?'on':'off')+'"></span>'+(s.key_set?'connected':'none');
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
    renderKB();
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
    tr.innerHTML='<td class="kbname" title="'+esc(d.name)+'">'+
        '<span class="fdot"></span>'+esc(d.name)+'</td>'+
      '<td><span class="corp">'+esc(d.corpus)+'</span></td>'+
      '<td class="num">'+(d.page_count||'—')+'</td>'+
      '<td class="num"><b>'+d.chunks.toLocaleString()+'</b></td>'+
      '<td>'+cts+'</td><td>'+meth+'</td>';
    tb.appendChild(tr);
  });
}

function openKey(){$('#keymodal').classList.add('show');}
function closeKey(){$('#keymodal').classList.remove('show');}
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
  html+='<span class="chip">'+cites.length+' sources</span></div>';
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
async function buildCommunities(btn){
  btn.disabled=true;const o=btn.innerHTML;btn.innerHTML='<span class="spin"></span> Building&hellip;';
  try{const r=await fetch('/api/communities/build',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
    toast('Built '+(r.communities||0)+' communities');refresh();}
  catch(e){toast('Build failed');}
  btn.disabled=false;btn.innerHTML=o;
}

let G=null,sim=null,svg=null,node=null,link=null,label=null;
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
    .on('click',(e,d)=>showNode(d))
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
</script>
</body>
</html>'''
