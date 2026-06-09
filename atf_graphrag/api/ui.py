"""Single-page browser UI. Lets you paste the OpenRouter API key, ingest data,
and ask questions — no file editing required. Served at GET /."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ATF GraphRAG Platform</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--line:#2a2f3a;--txt:#e6e8ec;--mut:#9aa3b2;
        --acc:#7f77dd;--ok:#1d9e75;--warn:#d85a30;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt)}
  header{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
  header h1{font-size:17px;margin:0}
  .badge{font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--line);color:var(--mut)}
  .badge.on{color:#fff;background:var(--ok);border-color:var(--ok)}
  .badge.off{color:#fff;background:var(--warn);border-color:var(--warn)}
  main{max-width:980px;margin:0 auto;padding:22px;display:grid;gap:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 12px}
  label{display:block;font-size:12px;color:var(--mut);margin:8px 0 4px}
  input,textarea,select,button{font:inherit;color:var(--txt);background:#0d0f14;border:1px solid var(--line);border-radius:8px;padding:9px 11px;width:100%}
  textarea{min-height:84px;resize:vertical}
  button{background:var(--acc);border-color:var(--acc);color:#fff;cursor:pointer;width:auto;padding:9px 16px;font-weight:600}
  button.sec{background:transparent;color:var(--txt)}
  button:hover{filter:brightness(1.08)}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
  .row>div{flex:1;min-width:160px}
  .muted{color:var(--mut);font-size:12px}
  pre{white-space:pre-wrap;background:#0d0f14;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:12.5px;overflow:auto}
  .answer{white-space:pre-wrap;line-height:1.6}
  .pill{display:inline-block;font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:20px;padding:2px 9px;margin:3px 4px 0 0}
  .cite{font-size:12px;color:var(--mut);border-left:2px solid var(--acc);padding:4px 10px;margin:6px 0}
  a{color:var(--acc)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:700px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>ATF GraphRAG Platform</h1>
  <span id="profile" class="badge">profile</span>
  <span id="keystate" class="badge off">key: not set</span>
  <span id="llmstate" class="badge">llm</span>
</header>
<main>

  <div class="card">
    <h2>1 · Connection — OpenRouter API key</h2>
    <p class="muted">Paste your key (from <a href="https://openrouter.ai/keys" target="_blank">openrouter.ai/keys</a>).
      It is kept in this browser and sent to your local app only. Without a key the app still runs in offline mode.</p>
    <div class="row">
      <div style="flex:2"><label>OpenRouter API key</label>
        <input id="key" type="password" placeholder="sk-or-..."/></div>
      <div><label>Model</label>
        <input id="model" placeholder="openai/gpt-4o-mini"/></div>
      <div style="flex:0"><label>&nbsp;</label><button onclick="saveKey()">Save key</button></div>
    </div>
    <p id="keymsg" class="muted"></p>
  </div>

  <div class="card">
    <h2>2 · Ingest data</h2>
    <div class="row">
      <div style="flex:0"><button class="sec" onclick="loadSample()">Load bundled ATF sample</button></div>
      <div><label>Corpus</label>
        <select id="corpus"><option>pdf</option><option>web</option><option>connected</option><option>visual</option></select></div>
    </div>
    <label>Paste text to ingest</label>
    <textarea id="text" placeholder="Paste an ATF report / record / web text..."></textarea>
    <div class="row" style="margin-top:8px"><div style="flex:0"><button onclick="ingestText()">Ingest text</button></div>
      <div class="muted" style="align-self:center">Or ingest a file on the server: <code>POST /ingest {"path":"..."}</code></div></div>
    <p id="ingmsg" class="muted"></p>
  </div>

  <div class="card">
    <h2>3 · Ask a question</h2>
    <label>Question</label>
    <input id="q" placeholder="How is Marcus Webb connected to Eagle Point Firearms?"
           onkeydown="if(event.key==='Enter')ask()"/>
    <div class="row" style="margin-top:8px">
      <div style="flex:0"><button onclick="ask()">Ask</button></div>
      <div style="flex:0"><label style="margin:0"><input type="checkbox" id="trace" style="width:auto" checked/> show trace</label></div>
    </div>
    <div id="result"></div>
  </div>

  <div class="card">
    <h2>4 · Engine status</h2>
    <div class="row"><div style="flex:0"><button class="sec" onclick="refresh()">Refresh</button></div></div>
    <div class="grid2"><pre id="stats">—</pre><pre id="graph">—</pre></div>
  </div>

</main>
<script>
const $ = id => document.getElementById(id);
function api(path, body){
  return fetch(path, body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined)
    .then(r=>r.json());
}
async function saveKey(){
  const key=$('key').value.trim(), model=$('model').value.trim();
  localStorage.setItem('or_key',key); localStorage.setItem('or_model',model);
  $('keymsg').textContent='Saving...';
  const r=await api('/api/key',{key,model});
  $('keymsg').textContent = r.error? ('Error: '+r.error) : ('Saved. LLM provider is now: '+r.llm);
  refresh();
}
async function loadSample(){
  $('ingmsg').textContent='Ingesting bundled sample...';
  const r=await api('/ingest',{dir:'data/sample',corpus:'pdf'});
  $('ingmsg').textContent='Indexed: '+JSON.stringify(r.indexed||r); refresh();
}
async function ingestText(){
  const text=$('text').value.trim(); if(!text)return;
  $('ingmsg').textContent='Ingesting...';
  const r=await api('/ingest',{text,corpus:$('corpus').value});
  $('ingmsg').textContent='Indexed chunks: '+JSON.stringify(r.indexed!==undefined?r.indexed:r); refresh();
}
async function ask(){
  const question=$('q').value.trim(); if(!question)return;
  $('result').innerHTML='<p class="muted">Thinking...</p>';
  const r=await api('/query',{question,trace:$('trace').checked});
  if(r.error){$('result').innerHTML='<p class="muted">Error: '+r.error+'</p>';return;}
  let html='<p><span class="pill">intent: '+r.intent+'</span>'+
           '<span class="pill">confidence: '+r.confidence+'</span>'+
           '<span class="pill">evidence: '+r.evidence_count+'</span></p>';
  html+='<div class="answer">'+escapeHtml(r.answer)+'</div>';
  if(r.graph_paths&&r.graph_paths.length){html+='<p class="muted" style="margin-top:10px">Relationship paths:</p>';
    r.graph_paths.forEach(p=>html+='<span class="pill">'+escapeHtml(p)+'</span>');}
  if(r.citations&&r.citations.length){html+='<p class="muted" style="margin-top:10px">Citations:</p>';
    r.citations.forEach(c=>html+='<div class="cite">['+c.ref+'] '+escapeHtml(c.source||'')+
      (c.page?(' p.'+c.page):'')+' · '+c.corpus+' · conf '+(c.confidence||0)+'</div>');}
  if(r.trace){html+='<p class="muted" style="margin-top:10px">Pipeline trace:</p><pre>'+
    escapeHtml(JSON.stringify(r.trace,null,2))+'</pre>';}
  $('result').innerHTML=html;
}
async function refresh(){
  const s=await api('/stats'); $('stats').textContent=JSON.stringify(s,null,2);
  $('profile').textContent='profile: '+(s.profile||'?');
  $('llmstate').textContent='llm: '+(s.llm||'?');
  const hasKey=s.llm && !s.llm.startsWith('offline');
  $('keystate').textContent = hasKey?'key: active':'key: not set';
  $('keystate').className='badge '+(hasKey?'on':'off');
  const g=await api('/graph/top'); $('graph').textContent=JSON.stringify(g,null,2);
}
function escapeHtml(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
// restore saved key on load and re-apply to the running server
(async function(){
  const k=localStorage.getItem('or_key')||'', m=localStorage.getItem('or_model')||'';
  if(k){$('key').value=k;$('model').value=m; await api('/api/key',{key:k,model:m});}
  refresh();
})();
</script>
</body>
</html>
"""
