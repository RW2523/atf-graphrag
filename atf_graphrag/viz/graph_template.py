"""Self-contained D3 graph viewer served at /graph/view (Step 6d).

Loads /graph/export, colours nodes by entity type, sizes by degree, groups by
community; click highlights a node's neighbourhood and shows its description +
connection count + the source documents (chunk_ids) it came from, so a visible
connection is verifiable. Type filters + name search included.
"""

GRAPH_VIEW_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>ATF GraphRAG — Explorer</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  body{margin:0;font:14px system-ui,Segoe UI,Arial;background:#0f1117;color:#e6e6e6}
  #bar{padding:8px 12px;background:#171a23;border-bottom:1px solid #262a36;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  #bar input,#bar select{background:#0f1117;color:#e6e6e6;border:1px solid #2a2f3c;border-radius:6px;padding:5px 8px}
  #wrap{display:flex;height:calc(100vh - 49px)}
  svg{flex:1;background:#0f1117}
  #side{width:330px;background:#141722;border-left:1px solid #262a36;padding:14px;overflow:auto}
  .muted{color:#8b93a7}.pill{display:inline-block;padding:2px 8px;border-radius:10px;background:#222838;margin:2px 4px 2px 0;font-size:12px}
  a{color:#6db3ff}.legend span{margin-right:10px}
  h3{margin:6px 0}.src{font-size:12px;color:#9fb0c9;word-break:break-all}
</style></head><body>
<div id="bar">
  <strong>ATF GraphRAG Explorer</strong>
  <input id="search" placeholder="search entity…" size="20">
  <select id="typeFilter"><option value="">all types</option></select>
  <span id="stats" class="muted"></span>
  <span class="legend" id="legend"></span>
</div>
<div id="wrap"><svg></svg>
  <div id="side"><p class="muted">Click a node to inspect its connections and source documents.</p></div>
</div>
<script>
const color = d3.scaleOrdinal(d3.schemeTab10);
let DATA=null, sim=null, node=null, link=null, label=null;

fetch('/graph/export').then(r=>r.json()).then(g=>{
  DATA=g; init(g);
});

function init(g){
  const types=[...new Set(g.nodes.map(n=>n.type))].sort();
  const tf=document.getElementById('typeFilter');
  types.forEach(t=>{const o=document.createElement('option');o.value=t;o.textContent=t;tf.appendChild(o);});
  document.getElementById('legend').innerHTML=types.map(t=>`<span style="color:${color(t)}">●${t}</span>`).join('');
  document.getElementById('stats').textContent=`${g.stats.nodes} nodes · ${g.stats.edges} edges · ${g.stats.communities} communities`+(g.stats.truncated?' (truncated)':'');
  draw(g);
}

function draw(g){
  const svg=d3.select('svg'), W=svg.node().clientWidth, H=svg.node().clientHeight;
  svg.selectAll('*').remove();
  const root=svg.append('g');
  svg.call(d3.zoom().scaleExtent([0.1,4]).on('zoom',e=>root.attr('transform',e.transform)));
  const adj={}; g.edges.forEach(e=>{(adj[e.source]=adj[e.source]||new Set()).add(e.target);(adj[e.target]=adj[e.target]||new Set()).add(e.source);});

  link=root.append('g').attr('stroke','#33384a').attr('stroke-opacity',.6)
    .selectAll('line').data(g.edges).join('line')
    .attr('stroke-width',d=>d.typed?2:1).attr('stroke',d=>d.typed?'#5b7cfa':'#33384a');

  node=root.append('g').selectAll('circle').data(g.nodes).join('circle')
    .attr('r',d=>4+Math.sqrt(d.degree))
    .attr('fill',d=>color(d.type)).attr('stroke','#0f1117').attr('stroke-width',1)
    .style('cursor','pointer').on('click',(e,d)=>inspect(d,adj))
    .call(d3.drag()
      .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;})
      .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y;})
      .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}));
  node.append('title').text(d=>`${d.name} (${d.type}) — ${d.degree} connections`);

  label=root.append('g').selectAll('text').data(g.nodes.filter(d=>d.degree>=3)).join('text')
    .text(d=>d.name).attr('font-size',10).attr('fill','#9fb0c9').attr('dx',8).attr('dy',3);

  sim=d3.forceSimulation(g.nodes)
    .force('link',d3.forceLink(g.edges).id(d=>d.id).distance(60))
    .force('charge',d3.forceManyBody().strength(-90))
    .force('center',d3.forceCenter(W/2,H/2))
    .on('tick',()=>{
      link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
      node.attr('cx',d=>d.x).attr('cy',d=>d.y);
      label.attr('x',d=>d.x).attr('y',d=>d.y);
    });
}

function inspect(d,adj){
  const neigh=adj[d.id]?[...adj[d.id]]:[];
  node.attr('opacity',n=>(n.id===d.id||adj[d.id]&&adj[d.id].has(n.id))?1:0.12);
  link.attr('opacity',e=>(e.source.id===d.id||e.target.id===d.id)?0.9:0.05);
  const comm=(DATA.communities&&DATA.communities[d.community]!==undefined)?DATA.communities[d.community]:null;
  const srcLinks=(d.chunk_ids||[]).map(c=>`<div class="src">• ${c}</div>`).join('')||'<span class="muted">none</span>';
  document.getElementById('side').innerHTML=
    `<h3>${d.name}</h3><div class="muted">${d.type} · ${d.degree} connections · community ${d.community}</div>`+
    (comm?`<p><strong>Community briefing</strong><br><span class="muted">${comm}</span></p>`:'')+
    `<p><strong>Connected to (${neigh.length})</strong><br>${neigh.slice(0,40).map(n=>`<span class="pill">${(DATA.nodes.find(x=>x.id===n)||{}).name||n}</span>`).join('')}</p>`+
    `<p><strong>Source chunks</strong> <span class="muted">(verify connection)</span>${srcLinks}</p>`;
}

document.getElementById('search').addEventListener('input',e=>{
  const q=e.target.value.toLowerCase();
  if(node)node.attr('opacity',n=>!q||n.name.toLowerCase().includes(q)?1:0.1);
});
document.getElementById('typeFilter').addEventListener('change',e=>{
  const t=e.target.value;
  if(node)node.attr('opacity',n=>!t||n.type===t?1:0.1);
});
</script></body></html>"""
