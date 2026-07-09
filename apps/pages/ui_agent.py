"""Agents tab — visualise the agent tree, inspect what each agent/tool does, and
edit an agent's system prompt.

The tree (Explorer or zoomable Diagram) and the click-to-inspect panels are
self-contained HTML components fed by the server's `tree` dict (which now carries
each tool's docstring and each agent's system prompt). Editing the system prompt
goes through /agent_definition/update (versioned in DuckDB); model/description are
shown read-only. No ADK, no disk — all over the API.
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


def _tree_depth(node: dict) -> int:
    return 1 + max((_tree_depth(c) for c in node.get("children") or []), default=0)


def _agent_details_map(tree: dict) -> dict:
    """name -> {model, description, tools, connects} for every agent in the tree.
    Used by the editor's read-only 'tools it can access' caption."""
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


# Shared detail renderer injected into both tree components (via __DETAILJS__), so
# the click-to-inspect panel is identical whether you use the Explorer or Diagram
# view. Shows an agent's description, its System prompt, and each tool's docstring;
# for a tool node, its docstring. Raw string so the /\n/ regex reaches JS intact.
_DETAIL_JS = r"""
  function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
  function br(s){return esc(s).replace(/\n/g,'<br>')}
  function relOf(n){return n.relation&&n.relation.indexOf('AgentTool')>=0?'call':(n.relation?'transfer':'')}
  function renderDetail(n){
    if(!n)return '';
    if(n.kind!=='agent'){
      return '<div class="title">&#128295; '+esc(n.name)+'</div><div class="sec">Tool &middot; what it does</div>'
        + (n.description?'<p>'+br(n.description)+'</p>':'<span class="dim">A capability an agent can call.</span>');
    }
    const tools=(n.children||[]).filter(c=>c.kind==='tool'), subs=(n.children||[]).filter(c=>c.kind==='agent');
    let h='<div class="title">&#129504; '+esc(n.name)+(n.model?'<span class="ptag">'+esc(n.model)+'</span>':'')+'</div>';
    if(n.description)h+='<p class="dim">'+esc(n.description)+'</p>';
    if(n.instruction)h+='<div class="sec">System prompt</div><pre class="prompt">'+esc(n.instruction)+'</pre>';
    h+='<div class="sec">Tools it can access</div>';
    h+=tools.length?'<ul>'+tools.map(t=>'<li>&#128295; <b>'+esc(t.name)+'</b>'+(t.description?'<br><span class="dim">'+br(t.description)+'</span>':'')+'</li>').join('')+'</ul>':'<span class="dim">none</span>';
    if(subs.length)h+='<div class="sec">Connects to agents</div><ul>'+subs.map(s=>'<li>&#129504; '+esc(s.name)+' <span class="ptag">'+relOf(s)+'</span></li>').join('')+'</ul>';
    return h;
  }
"""

# Shared panel styling for both components.
_PANEL_CSS = """
  #panel .title{font-size:15px;font-weight:600}
  #panel .ptag{display:inline-block;background:#1f6feb33;color:#79c0ff;border-radius:5px;padding:1px 6px;font-size:11px;margin-left:4px}
  #panel .dim{color:#8b949e} #panel p{margin:4px 0;line-height:1.4}
  #panel .sec{margin-top:10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#8b949e}
  #panel .prompt{white-space:pre-wrap;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px;font-size:12px;margin:4px 0;max-height:220px;overflow:auto}
  #panel ul{margin:4px 0 0;padding-left:18px} #panel li{margin:3px 0}
"""


# Zoomable + clickable agent tree. mermaid renders the server's flowchart; svg-pan-zoom
# adds wheel-zoom / drag-pan; a near-stationary mouseup on a node (a click, not a pan)
# opens its detail panel via renderDetail(NODES[name]). Self-contained — no callback.
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
  #panel{flex:1 1 0;min-width:200px;max-width:360px;border:1px solid #30363d;border-radius:10px;background:#0e1117;padding:12px;overflow:auto;font-size:13px}
  __PANEL_CSS__
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
  <div id="panel"><span class="dim">Click an agent or tool to see what it does.</span></div>
</div>
<script>
  const CODE=__CODE__, TREE=__TREE__; let PZ=null, downXY=null;
  const NODES={}; (function idx(n){NODES[n.name]=n;(n.children||[]).forEach(idx)})(TREE);
  __DETAILJS__
  function _zi(){PZ&&PZ.zoomBy(1.3)} function _zo(){PZ&&PZ.zoomBy(1/1.3)} function _rv(){if(PZ){PZ.resetZoom();PZ.center();PZ.fit()}}
  function show(name,el){
    document.querySelectorAll('.node.sel').forEach(n=>n.classList.remove('sel')); if(el)el.classList.add('sel');
    document.getElementById('panel').innerHTML = renderDetail(NODES[name]) || '<span class="dim">'+esc(name)+'</span>';
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
      n.addEventListener('mouseup',e=>{ if(!downXY)return;
        if(Math.hypot(e.clientX-downXY[0],e.clientY-downXY[1])<5) show((n.textContent||'').replace(/[\\u{1F9E0}\\u{1F527}]/gu,'').trim(),n);
      });
    });
  })();
</script></body></html>
"""


def _zoomable_agent_tree(mermaid_code: str, tree: dict, height: int) -> None:
    html = (_TREE_HTML
            .replace("__HEIGHT__", str(int(height)))
            .replace("__PANEL_CSS__", _PANEL_CSS)
            .replace("__CODE__", json.dumps(mermaid_code))
            .replace("__TREE__", json.dumps(tree))
            .replace("__DETAILJS__", _DETAIL_JS))
    components.html(html, height=int(height) + 8, scrolling=False)


# Windows-Explorer-style tree: indented rows, expand/collapse chevrons, 🧠/🔧 icons,
# click a row to inspect via the same renderDetail. Renders straight from the tree
# dict — no mermaid, no CDN.
_EXPLORER_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box} body{margin:0;color:#fafafa;font-family:"Source Sans Pro",system-ui,sans-serif;font-size:13px}
  #wrap{display:flex;gap:8px;height:__HEIGHT__px}
  #tree{flex:1 1 0;border:1px solid #30363d;border-radius:10px;background:#0e1117;overflow:auto;padding:6px 0}
  #panel{flex:1 1 0;min-width:200px;max-width:360px;border:1px solid #30363d;border-radius:10px;background:#0e1117;padding:12px;overflow:auto}
  .row{display:flex;align-items:center;gap:4px;padding:3px 8px;cursor:pointer;white-space:nowrap;user-select:none}
  .row:hover{background:#161b22} .row.sel{background:#1f6feb33}
  .chev{width:14px;display:inline-block;text-align:center;color:#8b949e;transition:transform .1s} .chev.open{transform:rotate(90deg)}
  .ico{width:16px;text-align:center} .nm{font-weight:500} .tag{color:#8b949e;font-size:11px;margin-left:6px}
  .kids.collapsed{display:none}
  __PANEL_CSS__
</style></head><body>
<div id="wrap"><div id="tree"></div>
  <div id="panel"><span class="dim">Click an agent or tool to see what it does.</span></div>
</div>
<script>
  const TREE=__TREE__;
  __DETAILJS__
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
    row.addEventListener('click',()=>{document.querySelectorAll('.row.sel').forEach(x=>x.classList.remove('sel')); row.classList.add('sel'); document.getElementById('panel').innerHTML=renderDetail(node);});
  }
  build(TREE,0,document.getElementById('tree'));
  const first=document.querySelector('.row'); if(first)first.click();
</script></body></html>
"""


def _explorer_tree(tree: dict, height: int) -> None:
    html = (_EXPLORER_HTML
            .replace("__HEIGHT__", str(int(height)))
            .replace("__PANEL_CSS__", _PANEL_CSS)
            .replace("__TREE__", json.dumps(tree))
            .replace("__DETAILJS__", _DETAIL_JS))
    components.html(html, height=int(height) + 8, scrolling=False)


def render_agents_tab(app: str, backend_name: str, on_rebuilt) -> None:
    try:
        data = api_client.get_agents(app, backend_name)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't reach the server: {e}")
        return
    editable = data.get("editable", [])
    mermaid, tree = data.get("mermaid"), data.get("tree")

    # --- agent tree: every agent, its tools, and what each does -------------
    if tree:
        st.subheader("Agent tree")
        options = ["Tree", "Diagram"] if mermaid else ["Tree"]
        view = st.radio("view", options, horizontal=True,
                        label_visibility="collapsed", key=f"tree-view::{app}")
        if view == "Diagram":
            st.caption("🧠 agent · 🔧 tool · **transfer** vs **call**. Scroll to zoom, "
                       "drag to pan, click a node to see what it does.")
            _zoomable_agent_tree(mermaid, tree,
                                 min(700, max(220, _tree_depth(tree) * 95 + 30)))
        else:
            st.caption("Explorer tree — ▸ to expand/collapse, click a node to see its "
                       "description, system prompt, and tools.")
            _explorer_tree(tree, min(820, max(120, _count_nodes(tree) * 26 + 20)))
        st.divider()

    if not editable:
        st.info("This app's agents expose no editable prompt or model.")
        return

    # --- edit ONLY the system prompt; everything else is read-only ----------
    st.subheader("Edit system prompt")
    names = [a["name"] for a in editable]
    agent = st.selectbox(f"Agent in **{app}** (backend **{backend_name}**)", names,
                         key=f"agent_sel::{app}")
    defn = api_client.read_agent_def(app, agent, backend_name)
    live, overlay = defn.get("live") or {}, defn.get("overlay") or {}

    tags = [f"`{live.get('type', '?')}`"]
    if live.get("model"):
        tags.append(f"model `{live['model']}`")
    if "instruction" in overlay:
        tags.append("prompt overridden")
    st.caption(" · ".join(tags))
    if live.get("description"):
        st.caption(f"📝 {live['description']}")
    sel = (_agent_details_map(tree) if tree else {}).get(agent)
    if sel and sel["tools"]:
        st.caption("🔧 Tools it can access: " + ", ".join(f"`{t}`" for t in sel["tools"]))

    if live.get("instruction") is None:
        st.info("Structural agent — no system prompt to edit.")
    else:
        with st.form(f"agent::{app}::{agent}"):
            instr_val = st.text_area("System prompt", value=live.get("instruction") or "",
                                     key=f"instr::{app}::{agent}", height=280)
            saved = st.form_submit_button("💾 Save & apply", width="stretch", type="primary")
        if saved:
            # instruction only — model/description are left untouched server-side.
            api_client.update_agent_def(app, agent, instruction=instr_val,
                                        model=None, description=None)
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
                f"_prompt_: {_excerpt(v['instruction'])}")
            if c2.button("↩️ Restore", key=f"restore::{agent}::{v['version']}",
                         width="stretch"):
                api_client.restore_agent_def(app, agent, v["version"])
                on_rebuilt()
                st.rerun()
            st.divider()
