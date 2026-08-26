#!/usr/bin/env python3
"""Generate a self-contained interactive state-flow-graph page from a Crawljax
crawl's result.json (replacement for the aged CrawlOverview alchemy.js view).

Usage: python3 scripts/make_crawl_graph.py <crawl-with-inputs dir> [out.html]
Open the result over HTTP (screenshots load relative to the crawl dir):
    cd <crawl-with-inputs> && python3 -m http.server 8765
    open http://localhost:8765/graph.html
"""
import json
import sys
from pathlib import Path

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>State-flow graph</title>
<style>
  body { margin:0; font-family: -apple-system, Helvetica, sans-serif; display:flex; height:100vh; }
  #canvas-wrap { flex:1; position:relative; background:#f6f7f5; }
  canvas { display:block; }
  #panel { width:340px; border-left:1px solid #ddd; padding:14px 16px; overflow-y:auto; background:#fff; }
  #panel h2 { font-size:15px; margin:0 0 4px; }
  #panel .url { font-size:12px; color:#555; word-break:break-all; }
  #panel img { width:100%; border:1px solid #ddd; border-radius:4px; margin-top:10px; }
  #panel .hint { color:#888; font-size:13px; margin-top:8px; }
  #legend { position:absolute; left:12px; top:10px; font-size:13px; color:#444;
            background:rgba(255,255,255,.85); padding:6px 10px; border-radius:5px; }
</style></head>
<body>
<div id="canvas-wrap">
  <div id="legend"><b>__NSTATES__ states · __NEDGES__ transitions</b> — drag nodes, click for screenshot</div>
  <canvas id="c"></canvas>
</div>
<div id="panel"><h2>State-flow graph</h2>
  <div class="hint">Each node is a page state Crawljax discovered; each arrow is a user action (click) that led from one state to another. Click a node to see the page as the crawler saw it.</div>
  <div id="detail"></div>
</div>
<script>
const GRAPH = __GRAPH__;
const wrap = document.getElementById('canvas-wrap');
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
let W, H;
function resize(){ W = canvas.width = wrap.clientWidth; H = canvas.height = wrap.clientHeight; }
resize(); window.addEventListener('resize', resize);

const nodes = GRAPH.nodes.map((n,i)=>({ ...n,
  x: W/2 + Math.cos(i/GRAPH.nodes.length*2*Math.PI)*Math.min(W,H)*.33,
  y: H/2 + Math.sin(i/GRAPH.nodes.length*2*Math.PI)*Math.min(W,H)*.33,
  vx:0, vy:0 }));
const byName = Object.fromEntries(nodes.map(n=>[n.name,n]));
const links = GRAPH.links.filter(l=>byName[l.source]&&byName[l.target]);

let dragging=null, selected=null;
function tick(){
  for (const l of links){
    const a=byName[l.source], b=byName[l.target];
    const dx=b.x-a.x, dy=b.y-a.y, d=Math.hypot(dx,dy)||1;
    const f=(d-120)*0.004;
    a.vx+=dx/d*f; a.vy+=dy/d*f; b.vx-=dx/d*f; b.vy-=dy/d*f;
  }
  for (let i=0;i<nodes.length;i++) for (let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j];
    const dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy||1;
    const f=Math.min(1200/d2, .6);
    const d=Math.sqrt(d2);
    a.vx-=dx/d*f; a.vy-=dy/d*f; b.vx+=dx/d*f; b.vy+=dy/d*f;
  }
  for (const n of nodes){
    if(n===dragging) continue;
    n.vx+=(W/2-n.x)*0.0004; n.vy+=(H/2-n.y)*0.0004;
    n.vx*=.86; n.vy*=.86; n.x+=n.vx; n.y+=n.vy;
    n.x=Math.max(30,Math.min(W-30,n.x)); n.y=Math.max(30,Math.min(H-30,n.y));
  }
}
function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.strokeStyle='#b9c2ba'; ctx.fillStyle='#b9c2ba'; ctx.lineWidth=1.2;
  for (const l of links){
    const a=byName[l.source], b=byName[l.target];
    const dx=b.x-a.x, dy=b.y-a.y, d=Math.hypot(dx,dy)||1;
    const tx=b.x-dx/d*16, ty=b.y-dy/d*16;
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(tx,ty); ctx.stroke();
    const ang=Math.atan2(dy,dx);
    ctx.beginPath();
    ctx.moveTo(tx,ty);
    ctx.lineTo(tx-8*Math.cos(ang-.4), ty-8*Math.sin(ang-.4));
    ctx.lineTo(tx-8*Math.cos(ang+.4), ty-8*Math.sin(ang+.4));
    ctx.fill();
  }
  for (const n of nodes){
    const r = n.name==='index'?14:10;
    ctx.beginPath(); ctx.arc(n.x,n.y,r,0,7);
    ctx.fillStyle = n===selected ? '#c46a34' : (n.name==='index' ? '#1f7a5c' : '#3a8f6f');
    ctx.fill();
    ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.stroke();
    ctx.fillStyle='#333'; ctx.font='11px -apple-system, sans-serif'; ctx.textAlign='center';
    ctx.fillText(n.name, n.x, n.y-r-5);
  }
}
(function loop(){ tick(); draw(); requestAnimationFrame(loop); })();

function nodeAt(x,y){ return nodes.find(n=>Math.hypot(n.x-x,n.y-y)<14); }
canvas.addEventListener('mousedown', e=>{ dragging=nodeAt(e.offsetX,e.offsetY)||null; });
canvas.addEventListener('mousemove', e=>{ if(dragging){ dragging.x=e.offsetX; dragging.y=e.offsetY; dragging.vx=dragging.vy=0; }});
canvas.addEventListener('mouseup', e=>{
  const n=nodeAt(e.offsetX,e.offsetY);
  if(n && (!dragging || n===dragging)){
    selected=n;
    document.getElementById('detail').innerHTML =
      '<h2 style="margin-top:14px">'+n.name+'</h2>'+
      '<div class="url">'+(n.url||'')+'</div>'+
      '<img src="screenshots/'+n.name+'.png" alt="screenshot of '+n.name+'" onerror="this.style.display=\\'none\\'">';
  }
  dragging=null;
});
</script></body></html>
"""


def main() -> None:
    crawl_dir = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else crawl_dir / "graph.html"

    result = json.loads((crawl_dir / "result.json").read_text())
    nodes = [
        {"name": s["name"], "url": s.get("url", "")}
        for s in result["states"].values()
    ]
    links = [
        {"source": e["from"], "target": e["to"]}
        for e in result["edges"]
    ]
    graph = {"nodes": nodes, "links": links}

    html = (TEMPLATE
            .replace("__GRAPH__", json.dumps(graph))
            .replace("__NSTATES__", str(len(nodes)))
            .replace("__NEDGES__", str(len(links))))
    out.write_text(html)
    print(f"Wrote {out} ({len(nodes)} states, {len(links)} transitions)")


if __name__ == "__main__":
    main()
