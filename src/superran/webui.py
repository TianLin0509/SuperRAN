"""Shared, offline-safe actions for SuperRAN's generated HTML surfaces.

The specification mock and KPI workbench are standalone files.  They therefore
cannot assume a frontend build, a CDN, or even an HTTP origin.  This module emits
the same accessible toolbar and browser-native export logic into both pages:

* structured JSON/CSV downloads use Blob URLs and work from ``file://``;
* text copy prefers the Async Clipboard API and falls back to a temporary
  textarea for browsers that deny clipboard access to a local file;
* screenshot export serializes the self-contained DOM into an SVG
  ``foreignObject`` and converts it to PNG with ``canvas.toBlob``;
* Web Share is used only when available; otherwise the exact same summary is
  copied, so an unsupported API never turns into a silent no-op.

The generated pages remain the presentation layer.  Downloading or sharing never
changes simulation configuration or KPI values.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from html import escape
from typing import Any


def _safe_script_json(value: Any) -> str:
    """JSON safe inside an HTML ``script`` element."""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


_ICONS = {
    "copy": '<path d="M8 8h11v11H8z"/><path d="M5 16H4V4h12v1"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 20h16"/>',
    "camera": '<path d="M4 7h3l2-2h6l2 2h3v12H4z"/><circle cx="12" cy="13" r="3"/>',
    "share": '<circle cx="18" cy="5" r="2"/><circle cx="6" cy="12" r="2"/><circle cx="18" cy="19" r="2"/><path d="m8 11 8-5M8 13l8 5"/>',
    "print": '<path d="M7 8V3h10v5"/><path d="M7 17H4v-7h16v7h-3"/><path d="M7 14h10v7H7z"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{_ICONS[name]}</svg>'
    )


def action_css() -> str:
    """CSS for :func:`render_actions`; colours inherit each page's variables."""
    return """
.page-actions{position:sticky;top:10px;z-index:30;display:flex;align-items:center;
justify-content:space-between;gap:12px;margin:14px 0;padding:9px 10px;border:1px solid
var(--line);border-radius:13px;background:var(--panel);background:color-mix(in srgb,var(--panel) 94%,transparent);
box-shadow:0 10px 28px rgba(15,23,42,.08);backdrop-filter:blur(14px)}
.page-actions .action-copy{display:flex;align-items:center;gap:9px;min-width:0;color:var(--muted);
font-size:12px}.page-actions .action-copy b{color:var(--ink);font-size:13px;white-space:nowrap}
.page-actions .action-buttons{display:flex;align-items:center;justify-content:flex-end;gap:7px;
flex-wrap:wrap}.action-btn,.download-menu>summary,.download-menu button{appearance:none;border:1px solid
var(--line);background:var(--panel);color:var(--ink);border-radius:9px;min-height:36px;padding:7px 10px;
font:650 12px/1.1 inherit;cursor:pointer;display:inline-flex;align-items:center;gap:6px;
transition:border-color .18s,background .18s,color .18s,box-shadow .18s}
.action-btn:hover,.download-menu>summary:hover,.download-menu button:hover{border-color:var(--blue);
color:var(--blue);box-shadow:0 4px 12px rgba(23,105,170,.12)}
.action-btn:focus-visible,.download-menu>summary:focus-visible,.download-menu button:focus-visible{
outline:3px solid var(--cyan);outline-offset:2px}.action-btn svg,.download-menu svg{width:17px;height:17px;
flex:0 0 auto}.download-menu{position:relative}.download-menu>summary{list-style:none}.download-menu>summary::-webkit-details-marker{display:none}
.download-menu[open]>summary{border-color:var(--blue);color:var(--blue)}.download-popover{position:absolute;
right:0;top:43px;z-index:40;min-width:220px;padding:7px;border:1px solid var(--line);border-radius:11px;
background:var(--panel);box-shadow:0 16px 38px rgba(15,23,42,.18)}.download-menu button{width:100%;
justify-content:flex-start;border-color:transparent;background:transparent}.action-status{position:fixed;right:22px;
bottom:22px;z-index:100;max-width:min(420px,calc(100vw - 32px));padding:11px 14px;border-radius:10px;
background:var(--ink);color:var(--panel);font-size:13px;box-shadow:0 12px 32px rgba(0,0,0,.25);
opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .18s,transform .18s}
.action-status.show{opacity:1;transform:none}.capture-stage{position:fixed;left:-100000px;top:0;z-index:-1}
@media(max-width:720px){.page-actions{position:relative;top:auto;align-items:flex-start;flex-direction:column}
.page-actions .action-buttons{width:100%;justify-content:flex-start}.page-actions .action-copy{width:100%}
.action-btn span,.download-menu>summary span{display:none}.action-btn,.download-menu>summary{padding:8px 10px}}
@media(prefers-reduced-motion:reduce){.action-btn,.download-menu>summary,.download-menu button,.action-status{transition:none}}
@media print{.page-actions,.action-status{display:none!important}}
"""


def render_actions(
    *,
    title: str,
    context: str,
    summary_text: str,
    root_selector: str,
    base_filename: str,
    downloads: Mapping[str, tuple[str, str, str]],
) -> str:
    """Return a toolbar plus self-contained action script.

    ``downloads`` maps a stable key to ``(label, mime_type, text_content)``.
    The script never fetches a URL, so all actions keep working after the HTML is
    copied to another machine and opened directly.
    """
    payload = {
        "title": str(title),
        "context": str(context),
        "summary": str(summary_text),
        "root": str(root_selector),
        "base": str(base_filename),
        "downloads": {
            str(key): {"label": str(label), "mime": str(mime), "content": str(content)}
            for key, (label, mime, content) in downloads.items()
        },
    }
    download_buttons = "".join(
        f'<button type="button" data-download="{escape(str(key), quote=True)}">'
        f'{_icon("download")}<span>{escape(str(label))}</span></button>'
        for key, (label, _mime, _content) in downloads.items()
    )
    toolbar = f"""
<nav class="page-actions no-export" aria-label="复制、导出与分享">
  <div class="action-copy"><b>{escape(str(context))}</b><span>页面离线可用 · 导出不改变任何仿真值</span></div>
  <div class="action-buttons">
    <button class="action-btn" type="button" data-action="copy">{_icon('copy')}<span>复制摘要</span></button>
    <details class="download-menu"><summary>{_icon('download')}<span>下载数据</span></summary>
      <div class="download-popover">{download_buttons}</div>
    </details>
    <button class="action-btn" type="button" data-action="screenshot">{_icon('camera')}<span>页面截图</span></button>
    <button class="action-btn" type="button" data-action="share">{_icon('share')}<span>分享</span></button>
    <button class="action-btn" type="button" data-action="print">{_icon('print')}<span>打印 / PDF</span></button>
  </div>
</nav>
<div class="action-status no-export" id="action-status" role="status" aria-live="polite"></div>
<script type="application/json" id="page-action-data">{_safe_script_json(payload)}</script>
"""
    return toolbar + """
<script>
(()=>{'use strict';
const data=JSON.parse(document.getElementById('page-action-data').textContent);
const status=document.getElementById('action-status');let statusTimer=0,lastImage=null;
function say(text){status.textContent=text;status.classList.add('show');clearTimeout(statusTimer);
  statusTimer=setTimeout(()=>status.classList.remove('show'),2600)}
function safeName(name){return String(name||'superran').replace(/[^a-zA-Z0-9._-]+/g,'-').replace(/^-+|-+$/g,'')||'superran'}
function saveBlob(blob,name){const a=document.createElement('a'),url=URL.createObjectURL(blob);
  a.href=url;a.download=safeName(name);document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500)}
async function copyText(text){if(window.isSecureContext&&navigator.clipboard&&navigator.clipboard.writeText){
  try{await navigator.clipboard.writeText(text);return true}catch(_e){}}
  const t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');t.style.position='fixed';
  t.style.left='-100000px';document.body.appendChild(t);t.select();let ok=false;
  try{ok=document.execCommand('copy')}catch(_e){}t.remove();return ok}
function checkedState(source,clone){const src=source.querySelectorAll('input,textarea,select,details');
  const dst=clone.querySelectorAll('input,textarea,select,details');for(let i=0;i<Math.min(src.length,dst.length);i++){
    const a=src[i],b=dst[i];if(a.tagName==='INPUT'){if(a.checked)b.setAttribute('checked','');else b.removeAttribute('checked');
      if(a.value!==undefined)b.setAttribute('value',a.value)}else if(a.tagName==='TEXTAREA'){b.textContent=a.value}
    else if(a.tagName==='SELECT'){Array.from(b.options).forEach((o,j)=>{if(j===a.selectedIndex)o.setAttribute('selected','');else o.removeAttribute('selected')})}
    else if(a.tagName==='DETAILS'){if(a.open)b.setAttribute('open','');else b.removeAttribute('open')}}}
async function captureImage(){const source=document.querySelector(data.root);if(!source)throw new Error('找不到截图区域');
  const clone=source.cloneNode(true);checkedState(source,clone);clone.querySelectorAll('.no-export,script').forEach(x=>x.remove());
  clone.style.width=Math.ceil(source.getBoundingClientRect().width)+'px';clone.style.margin='0';
  const stage=document.createElement('div');stage.className='capture-stage';stage.style.width=clone.style.width;
  stage.appendChild(clone);document.body.appendChild(stage);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
  const width=Math.ceil(Math.max(clone.scrollWidth,clone.getBoundingClientRect().width));
  const height=Math.ceil(Math.max(clone.scrollHeight,clone.getBoundingClientRect().height));stage.remove();
  const NL=String.fromCharCode(10);
  const styles=Array.from(document.querySelectorAll('style')).map(x=>x.textContent).join(NL);
  const markup=new XMLSerializer().serializeToString(clone);
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+width+'" height="'+height+'">'
    +'<foreignObject width="100%" height="100%"><div xmlns="http://www.w3.org/1999/xhtml">'
    +'<style>'+styles+'</style>'+markup+'</div></foreignObject></svg>';
  const svgBlob=new Blob([svg],{type:'image/svg+xml;charset=utf-8'}),svgUrl=URL.createObjectURL(svgBlob);
  try{const image=new Image();image.src=svgUrl;await image.decode();
    const maxDim=16000,maxPixels=64000000;let scale=Math.min(2,maxDim/width,maxDim/height,Math.sqrt(maxPixels/(width*height)));
    scale=Math.max(.35,scale);const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(width*scale));
    canvas.height=Math.max(1,Math.round(height*scale));const ctx=canvas.getContext('2d');
    ctx.fillStyle=getComputedStyle(document.body).backgroundColor||'#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.drawImage(image,0,0,canvas.width,canvas.height);
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('PNG 编码失败')),'image/png'));
    return {blob:blob,ext:'png',mime:'image/png'}
  }catch(error){console.info('PNG screenshot blocked; using SVG fallback',error);return {blob:svgBlob,ext:'svg',mime:'image/svg+xml'}}
  finally{URL.revokeObjectURL(svgUrl)}}
document.addEventListener('click',async event=>{const download=event.target.closest('[data-download]');
  if(download){const item=data.downloads[download.dataset.download];if(!item)return;saveBlob(new Blob([item.content],{type:item.mime}),data.base+'-'+download.dataset.download);download.closest('details').removeAttribute('open');say('已下载 '+item.label);return}
  const button=event.target.closest('[data-action]');if(!button)return;button.disabled=true;
  try{if(button.dataset.action==='copy'){say(await copyText(data.summary)?'摘要已复制':'浏览器拒绝剪贴板，请手动复制')}
    else if(button.dataset.action==='print'){window.print()}
    else if(button.dataset.action==='screenshot'){const image=await captureImage();lastImage=image;
      saveBlob(image.blob,data.base+'-screenshot.'+image.ext);let copied=false;
      if(image.ext==='png'&&window.isSecureContext&&navigator.clipboard&&window.ClipboardItem){try{await navigator.clipboard.write([new ClipboardItem({'image/png':image.blob})]);copied=true}catch(_e){}}
      say(copied?'PNG 已下载并复制到剪贴板':image.ext.toUpperCase()+' 页面截图已下载')}
    else if(button.dataset.action==='share'){const shareData={title:data.title,text:data.summary};
      if(location.protocol==='http:'||location.protocol==='https:')shareData.url=location.href;
      if(lastImage&&navigator.canShare){const file=new File([lastImage.blob],data.base+'-screenshot.'+lastImage.ext,{type:lastImage.mime});
        if(navigator.canShare({files:[file]}))shareData.files=[file]}
      if(navigator.share){try{await navigator.share(shareData);say('已打开系统分享');return}catch(e){if(e&&e.name==='AbortError')return}}
      say(await copyText(data.summary+(shareData.url?String.fromCharCode(10)+shareData.url:''))?'当前浏览器不支持系统分享，摘要已复制':'当前浏览器不支持分享')}
  }catch(error){console.error(error);say('操作失败：'+(error&&error.message?error.message:error))}finally{button.disabled=false}},false);
document.addEventListener('click',event=>{document.querySelectorAll('.download-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.removeAttribute('open')})});
})();
</script>
"""


__all__ = ["action_css", "render_actions"]
