'use strict';
const $ = id => document.getElementById(id);
const fields = {title:'工作',status:'状态',progress:'最新进展',result_url:'成果链接'};
const sources = {human:'人工修改',codex:'Codex 更新','company-agent':'公司 agent 更新',agent:'Agent 更新'};
let state, current, currentHistory=[], timer, fetching=false, pending=null;
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = stamp => new Date(stamp*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const draftKey = id => `superran:${location.pathname}:${state.me.id}:${id}`;
function localGet(key){try{return JSON.parse(localStorage.getItem(key));}catch{return null;}}
function localSet(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{$('draftNotice').textContent='浏览器无法保存草稿，请在关闭前复制文字。';return false;}}
function localRemove(key){try{localStorage.removeItem(key);}catch{}}
async function api(path,method='GET',body){
 const r=await fetch('api/'+path,{method,headers:{'Content-Type':'application/json','X-SuperRAN-Request':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
 const data=await r.json();
 if(!r.ok){const detail=data.detail;const error=new Error(typeof detail==='string'?detail:detail?.message||'请求未完成，请检查输入');error.status=r.status;error.detail=detail;throw error;}
 return data;
}
function render(){
 const names=Object.fromEntries(state.members.map(m=>[m.id,m.name]));

 const selected=$('memberFilter').value;
 $('memberFilter').innerHTML='<option value="">全部成员</option>'+state.members.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');$('memberFilter').value=selected;
 const q=$('search').value.trim().toLowerCase(),s=$('statusFilter').value,m=$('memberFilter').value;
 const works=state.works.filter(w=>(!s||w.status===s)&&(!m||w.owner===m)&&(!q||(w.title+' '+w.progress+' '+names[w.owner]).toLowerCase().includes(q)));
 $('rows').innerHTML=works.length?works.map(w=>`<article class="work-row"><div><button class="work-title" data-work="${esc(w.id)}">${esc(w.title)}</button><div class="owner">${esc(names[w.owner]||'成员')}${w.owner===state.me.id?' · 我':''}</div></div><div><span class="status ${w.status==='受阻'?'blocked':w.status==='已完成'?'done':''}">${esc(w.status)}</span></div><div><div class="progress">${esc(w.progress||'等待更新进展')}</div>${w.result_url?`<a class="result" href="${esc(w.result_url)}" target="_blank" rel="noopener noreferrer">查看成果 ↗</a>`:''}${w.locks.length?'<div class="lock">含人工保护内容</div>':''}</div><div><div class="time">${esc(time(w.updated))}</div><div class="source">${esc(sources[w.source])}</div></div></article>`).join(''):`<div class="empty">${state.works.length?'没有符合条件的工作':'还没有工作记录。接入 agent 后，第一条进展会出现在这里。'}</div>`;
}
async function refresh(){
 if(fetching||document.hidden)return;fetching=true;
 try{state=await api('state');render();$('sync').textContent='已同步 · '+time(state.at);}
 catch(e){$('sync').textContent='同步中断 · 显示上次内容';$('notice').textContent='暂时无法同步，已有记录和本地草稿仍保留。';}
 finally{fetching=false;}
}
function editable(){return true;}
function readForm(){return Object.fromEntries(Object.keys(fields).map(f=>[f,$('field-'+f).value]));}
function stash(){if(!editable())return;localSet(draftKey(current.id),{base:current,values:readForm(),release:[...document.querySelectorAll('[data-release]:checked')].map(e=>e.dataset.release),pending});if(!current.revision)localSet(draftKey('new'),current.id);}
function showEditor(work,history,restore=true){
 current=work;currentHistory=history;pending=null;const saved=restore?localGet(draftKey(work.id)):null;
 if(saved){current=saved.base;pending=saved.pending||null;}
 const values=saved?.values||current,can=editable();
 $('detailTitle').textContent=current.revision?'工作记录':'记录工作';$('detailMeta').textContent=`${state.members.find(m=>m.id===current.owner)?.name||state.me.name} · ${current.revision?'持续更新同一条记录':'创建后全组可见'}`;
 $('editFields').innerHTML=(current.revision?'':`<div class="field"><div class="field-head"><label for="workOwner">负责人</label></div><select id="workOwner" required><option value="">请选择</option>${state.members.map(m=>`<option value="${esc(m.id)}" ${m.id===current.owner?'selected':''}>${esc(m.name)}</option>`).join('')}</select></div>`)+Object.entries(fields).map(([f,label])=>`<div class="field"><div class="field-head"><label for="field-${f}">${label}</label>${current.locks.includes(f)?`<label class="release"><input type="checkbox" data-release="${f}" ${can?'':'disabled'}>人工保护 · 交回 agent</label>`:''}</div>${f==='status'?`<select id="field-${f}" ${can?'':'disabled'}>${['进行中','受阻','已完成'].map(s=>`<option ${s===values[f]?'selected':''}>${s}</option>`).join('')}</select>`:f==='progress'?`<textarea id="field-${f}" maxlength="12000" ${can?'':'disabled'}>${esc(values[f])}</textarea>`:`<input id="field-${f}" value="${esc(values[f])}" ${f==='title'?'required maxlength="160"':'type="url" maxlength="2000" placeholder="可选：报告或代码链接"'} ${can?'':'disabled'}>`}</div>`).join('');
 for(const f of saved?.release||[]){const box=document.querySelector(`[data-release="${f}"]`);if(box){box.checked=true;$('field-'+f).disabled=true;}}
 $('save').hidden=!can;$('editError').textContent='';$('reloadDetail').hidden=true;$('discardDraft').hidden=!saved;
 $('draftNotice').textContent=saved?'已恢复本机草稿；若其他人已更新，保存时会提示核对。':can?'保存的字段将受到人工保护。未保存草稿仅留在本机。':'全组可查看；本人和负责人可以修改。';
 $('historyRows').innerHTML=history.map(h=>`<div class="history-entry"><strong>${esc(time(h.at))} · ${esc(h.name)} · ${esc(sources[h.source])}</strong>${Object.entries(h.request.changes).map(([f,v])=>`<p>${esc(fields[f])}${h.result.protected.includes(f)?'（人工保护，未采用）':''}：${esc(v)}</p>`).join('')}${h.result.released.length?`<p>交回 agent：${esc(h.result.released.map(f=>fields[f]).join('、'))}</p>`:''}</div>`).join('')||'<p class="sub">暂无历史</p>';
 $('history').open=false;
 if(!$('detail').open)$('detail').showModal();
}
async function openWork(id){try{const d=await api('works/'+id);showEditor(d.work,d.history);}catch(e){$('notice').textContent=e.message;}}
$('rows').onclick=e=>{const b=e.target.closest('[data-work]');if(b)openWork(b.dataset.work);};
for(const id of ['search','statusFilter','memberFilter'])$(id).addEventListener('input',()=>state&&render());
$('newWork').onclick=()=>showEditor({id:localGet(draftKey('new'))||crypto.randomUUID(),owner:state.members[0]?.id||'',revision:0,title:'',status:'进行中',progress:'',result_url:'',locks:[]},[]);
$('closeDetail').onclick=()=>{stash();$('detail').close();};$('detail').addEventListener('cancel',()=>stash());
$('editFields').addEventListener('input',e=>{if(e.target.id==='workOwner')current.owner=e.target.value;if(e.target.dataset.release)$('field-'+e.target.dataset.release).disabled=e.target.checked;pending=null;stash();});
$('editForm').onsubmit=async e=>{
 e.preventDefault();const values=readForm(),release=[...document.querySelectorAll('[data-release]:checked')].map(x=>x.dataset.release);
 const changes=Object.fromEntries(Object.entries(values).filter(([f,v])=>!release.includes(f)&&(current.revision===0||v!==current[f])));
 if(!pending&&!Object.keys(changes).length&&!release.length){$('detail').close();return;}
 pending=pending||{event_id:crypto.randomUUID(),expected_revision:current.revision,changes,release,source:'human',...(current.revision?{}:{owner:current.owner})};stash();$('save').disabled=true;
 try{await api('works/'+current.id,'PUT',pending);localRemove(draftKey(current.id));if(localGet(draftKey('new'))===current.id)localRemove(draftKey('new'));pending=null;$('detail').close();$('notice').textContent='已保存，全组同步可见。';await refresh();}
 catch(err){$('editError').textContent=err.message+(err.status===409?'。点击“核对最新记录”查看差异后再保存。':'。草稿已保留，可以重试保存。');$('reloadDetail').hidden=err.status!==409;stash();}
 finally{$('save').disabled=false;}
};
$('reloadDetail').onclick=async()=>{
 try{const values=readForm(),old=current,latest=await api('works/'+current.id);const desired=Object.fromEntries(Object.entries(values).filter(([f,v])=>v!==old[f]));
 pending=null;localRemove(draftKey(current.id));showEditor(latest.work,latest.history,false);
 for(const [f,v] of Object.entries(desired))$('field-'+f).value=v;
 $('history').open=true;$('editError').textContent='已载入最新记录和历史，并保留你改过的字段。请核对后重新保存。';stash();
 }catch(e){$('editError').textContent=e.message;}
};
$('discardDraft').onclick=async()=>{localRemove(draftKey(current.id));if(current.revision)await openWork(current.id);else{localRemove(draftKey('new'));$('detail').close();}};
$('connect').onclick=()=>$('connectDialog').showModal();$('closeConnect').onclick=()=>$('connectDialog').close();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
window.addEventListener('beforeunload',()=>{if($('detail').open)stash();});
refresh();timer=setInterval(refresh,4000);
