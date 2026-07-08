"""Agents tab — pick one agent, edit its fields, see/restore its history.

Reads the agent's current fields (code default + live overlay) from the server,
saves edits through /agent_definition/update (which versions them in DuckDB and
syncs the live overlay), and lists past versions with a restore button. No ADK,
no disk — all over the API. `on_rebuilt()` refreshes the session after a change.
"""
from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from apps.pages import api_client


def _excerpt(text: str | None, n: int = 90) -> str:
    return (text or "").replace("\n", " ").strip()[:n] or "—"


def _count_nodes(node: dict) -> int:
    return 1 + sum(_count_nodes(c) for c in node.get("children", []))


def _agent_details_map(tree: dict) -> dict:
    """name -> {model, description, tools, connects} for every agent in the tree,
    so the diagram can show details for a clicked node without a server round-trip."""
    out: dict = {}

    def walk(node: dict) -> None:
        if node.get("kind") == "agent":
            out.setdefault(node["name"], {
                "model": node.get("model"),
                "description": node.get("description"),
                "tools": [c["name"] for c in node["children"] if c.get("kind") == "tool"],
                "connects": [
                    {"name": c["name"],
                     "rel": "call" if "AgentTool" in (c.get("relation") or "") else "transfer"}
                    for c in node["children"] if c.get("kind") == "agent"
                ],
            })
        for c in node.get("children", []):
            walk(c)

    walk(tree)
    return out


# Zoomable + clickable agent tree. mermaid renders the server's flowchart; svg-pan-zoom
# adds wheel-zoom / drag-pan; a near-stationary mouseup on a node (i.e. a click, not a
# pan) opens its detail panel from the DETAILS map. Self-contained — no Streamlit callback.
_TREE_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js"></script>
<style>
  *{box-sizing:border-box} body{margin:0;font-family:"Source Sans Pro",system-ui,sans-serif;color:#fafafa}
  #wrap{display:flex;gap:8px;height:__HEIGHT__px}
  #stage{position:relative;flex:2 1 0;border:1px solid #30363d;border-radius:10px;background:#0e1117;overflow:hidden}
  #diagram,#diagram svg{width:100%;height:100%}
  #controls{position:absolute;top:8px;right:8px;z-index:5;display:flex;gap:4px}
  #controls button{background:#21262d;color:#fafafa;border:1px solid #30363d;border-radius:6px;width:30px;height:30px;cursor:pointer;font-size:15px}
  #controls button:hover{background:#30363d}
  #panel{flex:1 1 0;min-width:180px;max-width:320px;border:1px solid #30363d;border-radius:10px;background:#0e1117;padding:12px;overflow:auto;font-size:13px}
  #panel .title{font-size:15px;font-weight:600}
  #panel .tag{display:inline-block;background:#1f6feb33;color:#79c0ff;border-radius:5px;padding:1px 6px;font-size:11px;margin-left:4px}
  #panel .dim{color:#8b949e}
  #panel .sec{margin-top:10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#8b949e}
  #panel ul{margin:4px 0 0;padding-left:18px} #panel li{margin:2px 0}
  .node{cursor:pointer} .node.sel>*:first-child{stroke:#f0b429 !important;stroke-width:3px !important}
</style></head><body>
<div id="wrap">
  <div id="stage">
    <div id="controls">
      <button title="Zoom in" onclick="_zi()">+</button>
      <button title="Zoom out" onclick="_zo()">-</button>
      <button title="Reset view" onclick="_rv()">&#8635;</button>
    </div>
    <div id="diagram"></div>
  </div>
  <div id="panel"><span class="dim">Click an agent to inspect it &mdash; its model, description, and the tools it can access.</span></div>
</div>
<script>
  const DETAILS=__DETAILS__, CODE=__CODE__; let PZ=null, downXY=null;
  function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function _zi(){PZ&&PZ.zoomBy(1.3)} function _zo(){PZ&&PZ.zoomBy(1/1.3)} function _rv(){if(PZ){PZ.resetZoom();PZ.center();PZ.fit()}}
  function show(name,el){
    document.querySelectorAll('.node.sel').forEach(n=>n.classList.remove('sel'));
    if(el)el.classList.add('sel');
    const info=DETAILS[name], p=document.getElementById('panel');
    if(!info){p.innerHTML='<div class="title">&#128295; '+esc(name)+'</div><div class="sec">Tool</div><span class="dim">A capability an agent can call.</span>';return;}
    let h='<div class="title">&#129504; '+esc(name)+(info.model?'<span class="tag">'+esc(info.model)+'</span>':'')+'</div>';
    if(info.description)h+='<p class="dim">'+esc(info.description)+'</p>';
    h+='<div class="sec">Tools it can access</div>';
    h+=info.tools.length?'<ul>'+info.tools.map(t=>'<li>&#128295; '+esc(t)+'</li>').join('')+'</ul>':'<span class="dim">none</span>';
    if(info.connects.length)h+='<div class="sec">Connects to agents</div><ul>'+info.connects.map(c=>'<li>&#129504; '+esc(c.name)+' <span class="tag">'+esc(c.rel)+'</span></li>').join('')+'</ul>';
    p.innerHTML=h;
  }
  (async()=>{
    mermaid.initialize({startOnLoad:false,theme:'dark',securityLevel:'loose'});
    const d=document.getElementById('diagram');
    const {svg}=await mermaid.render('agenttree',CODE);
    d.innerHTML=svg;
    const s=d.querySelector('svg'); s.setAttribute('width','100%'); s.setAttribute('height','100%'); s.style.maxWidth='none';
    PZ=svgPanZoom(s,{controlIconsEnabled:false,fit:true,center:true,minZoom:0.2,maxZoom:20,zoomScaleSensitivity:0.3});
    d.addEventListener('mousedown',e=>{downXY=[e.clientX,e.clientY]});
    d.querySelectorAll('.node').forEach(n=>{
      n.addEventListener('mouseup',e=>{
        if(!downXY)return;
        if(Math.hypot(e.clientX-downXY[0],e.clientY-downXY[1])<5)
          show((n.textContent||'').replace(/[\\u{1F9E0}\\u{1F527}]/gu,'').trim(),n);
      });
    });
  })();
</script></body></html>
"""


def _zoomable_agent_tree(mermaid_code: str, details: dict, height: int) -> None:
    html = (_TREE_HTML
            .replace("__HEIGHT__", str(int(height)))
            .replace("__DETAILS__", json.dumps(details))
            .replace("__CODE__", json.dumps(mermaid_code)))
    components.html(html, height=int(height) + 24, scrolling=False)


# Windows-Explorer-style tree: indented rows, ▸ expand/collapse chevrons, 🧠/🔧 icons,
# click a row to inspect. Renders straight from the server's `tree` dict — no mermaid,
# no CDN. Details (model / description / tools / connects) come off the clicked node.
_EXPLORER_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box} body{margin:0;color:#fafafa;font-family:"Source Sans Pro",system-ui,sans-serif;font-size:13px}
  #wrap{display:flex;gap:8px;height:__HEIGHT__px}
  #tree{flex:1 1 0;border:1px solid #30363d;border-radius:10px;background:#0e1117;overflow:auto;padding:6px 0}
  #panel{flex:1 1 0;min-width:180px;max-width:340px;border:1px solid #30363d;border-radius:10px;background:#0e1117;padding:12px;overflow:auto}
  .row{display:flex;align-items:center;gap:4px;padding:3px 8px;cursor:pointer;white-space:nowrap;user-select:none}
  .row:hover{background:#161b22} .row.sel{background:#1f6feb33}
  .chev{width:14px;display:inline-block;text-align:center;color:#8b949e;transition:transform .1s} .chev.open{transform:rotate(90deg)}
  .ico{width:16px;text-align:center} .nm{font-weight:500} .tag{color:#8b949e;font-size:11px;margin-left:6px}
  .kids.collapsed{display:none}
  .title{font-size:15px;font-weight:600}
  .ptag{display:inline-block;background:#1f6feb33;color:#79c0ff;border-radius:5px;padding:1px 6px;font-size:11px;margin-left:4px}
  .dim{color:#8b949e} .sec{margin-top:10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#8b949e}
  ul{margin:4px 0 0;padding-left:18px} li{margin:2px 0}
</style></head><body>
<div id="wrap"><div id="tree"></div>
  <div id="panel"><span class="dim">Click an agent to inspect it &mdash; its model, description, and the tools it can access.</span></div>
</div>
<script>
  const TREE=__TREE__;
  function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
  function relOf(n){return n.relation&&n.relation.indexOf('AgentTool')>=0?'call':(n.relation?'transfer':'')}
  function details(n){
    const p=document.getElementById('panel');
    if(n.kind!=='agent'){p.innerHTML='<div class="title">&#128295; '+esc(n.name)+'</div><div class="sec">Tool</div><span class="dim">A capability an agent can call.</span>';return;}
    const tools=(n.children||[]).filter(c=>c.kind==='tool'), subs=(n.children||[]).filter(c=>c.kind==='agent');
    let h='<div class="title">&#129504; '+esc(n.name)+(n.model?'<span class="ptag">'+esc(n.model)+'</span>':'')+'</div>';
    if(n.description)h+='<p class="dim">'+esc(n.description)+'</p>';
    h+='<div class="sec">Tools it can access</div>';
    h+=tools.length?'<ul>'+tools.map(t=>'<li>&#128295; '+esc(t.name)+'</li>').join('')+'</ul>':'<span class="dim">none</span>';
    if(subs.length)h+='<div class="sec">Connects to agents</div><ul>'+subs.map(s=>'<li>&#129504; '+esc(s.name)+' <span class="ptag">'+relOf(s)+'</span></li>').join('')+'</ul>';
    p.innerHTML=h;
  }
  function build(node,depth,container){
    const kids=node.children||[];
    const row=document.createElement('div'); row.className='row'; row.style.paddingLeft=(depth*16+6)+'px';
    const chev=document.createElement('span'); chev.className='chev'+(kids.length?' open':''); chev.textContent=kids.length?'\\u25B8':'';
    const ico=document.createElement('span'); ico.className='ico'; ico.textContent=node.kind==='agent'?'\\uD83E\\uDDE0':'\\uD83D\\uDD27';
    const nm=document.createElement('span'); nm.className='nm'; nm.textContent=node.name;
    const tag=document.createElement('span'); tag.className='tag';
    const bits=[]; if(node.model)bits.push(node.model); const r=relOf(node); if(r)bits.push(r);
    tag.textContent=bits.join('  \\u00B7  ');
    row.appendChild(chev); row.appendChild(ico); row.appendChild(nm); row.appendChild(tag);
    container.appendChild(row);
    let box=null;
    if(kids.length){ box=document.createElement('div'); box.className='kids'; container.appendChild(box); kids.forEach(k=>build(k,depth+1,box)); }
    chev.addEventListener('click',e=>{e.stopPropagation(); if(!box)return; const open=chev.classList.toggle('open'); box.classList.toggle('collapsed',!open);});
    row.addEventListener('click',()=>{document.querySelectorAll('.row.sel').forEach(x=>x.classList.remove('sel')); row.classList.add('sel'); details(node);});
  }
  build(TREE,0,document.getElementById('tree'));
  const first=document.querySelector('.row'); if(first)first.click();
</script></body></html>
"""


def _explorer_tree(tree: dict, height: int) -> None:
    html = (_EXPLORER_HTML
            .replace("__HEIGHT__", str(int(height)))
            .replace("__TREE__", json.dumps(tree)))
    components.html(html, height=int(height) + 24, scrolling=False)


def render_agents_tab(app: str, backend_name: str, on_rebuilt) -> None:
    try:
        data = api_client.get_agents(app, backend_name)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't reach the server: {e}")
        return
    editable = data.get("editable", [])
    mermaid, tree = data.get("mermaid"), data.get("tree")

    # --- agent tree: every agent and the tools it can reach -----------------
    if tree:
        st.subheader("Agent tree")
        options = ["Tree", "Diagram"] if mermaid else ["Tree"]
        view = st.radio("view", options, horizontal=True,
                        label_visibility="collapsed", key=f"tree-view::{app}")
        if view == "Diagram":
            st.caption("🧠 agent · 🔧 tool · **transfer** (hand off to a sub-agent) vs "
                       "**call** (invoke an agent-tool, keep control). "
                       "Scroll to zoom, drag to pan, click an agent to inspect it.")
            _zoomable_agent_tree(mermaid, _agent_details_map(tree),
                                 max(300, min(_count_nodes(tree) * 70, 820)))
        else:
            st.caption("Explorer-style tree — ▸ to expand/collapse, click a row to "
                       "inspect an agent and the tools it can access.")
            _explorer_tree(tree, max(300, min(_count_nodes(tree) * 40, 820)))
        st.divider()

    if not editable:
        st.info("This app's agents expose no editable prompt or model.")
        return

    names = [a["name"] for a in editable]
    agent = st.selectbox(f"Agent in **{app}** (backend **{backend_name}**)", names,
                         key=f"agent_sel::{app}")
    defn = api_client.read_agent_def(app, agent, backend_name)
    live, overlay = defn.get("live") or {}, defn.get("overlay") or {}
    pinned = [f for f in ("instruction", "model", "description") if f in overlay]
    st.caption(f"`{live.get('type', '?')}` · "
               + (f"overridden: {', '.join(pinned)}" if pinned else "all code defaults"))

    # --- edit ---------------------------------------------------------------
    with st.form(f"agent::{app}::{agent}"):
        has_instr = live.get("instruction") is not None
        instr_val = st.text_area("Instruction (system prompt)",
                                 value=live.get("instruction") or "",
                                 key=f"instr::{app}::{agent}", height=200,
                                 disabled=not has_instr) if has_instr else None
        if not has_instr:
            st.caption("_Structural agent — no editable prompt._")
        model_val = st.text_input(
            "Model override", value=overlay.get("model", ""),
            placeholder=f"default: {live.get('model') or '—'} (follows Backend)",
            key=f"model::{app}::{agent}",
            help="A concrete model id pins this agent; blank follows the Backend selector.")
        desc_val = st.text_input("Description", value=live.get("description") or "",
                                 key=f"desc::{app}::{agent}")
        saved = st.form_submit_button("💾 Save & apply", width="stretch", type="primary")

    if saved:
        api_client.update_agent_def(app, agent, instruction=instr_val,
                                    model=model_val.strip(), description=desc_val)
        on_rebuilt()
        st.rerun()

    # --- history ------------------------------------------------------------
    with st.expander("🕓 History", expanded=False):
        hist = api_client.agent_def_history(app, agent)
        if not hist:
            st.caption("No saved versions yet — edits you save here will show up.")
            return
        for v in hist:
            c1, c2 = st.columns([5, 1])
            c1.markdown(
                f"**v{v['version']}** · {v['updated_at'][:19]} · "
                f"model `{v['model'] or '—'}`  \n"
                f"_instr_: {_excerpt(v['instruction'])} · _desc_: {_excerpt(v['description'], 50)}")
            if c2.button("↩️ Restore", key=f"restore::{agent}::{v['version']}",
                         width="stretch"):
                api_client.restore_agent_def(app, agent, v["version"])
                on_rebuilt()
                st.rerun()
            st.divider()
