/* Real controls retain their own IDs, listeners, validation and values. */
let uiRefreshPending=false;
function enhanceRegisteredControls(){
 document.querySelectorAll('select').forEach(sel=>{
  if(sel.options.length>=10&&!sel.dataset.searchReady){
   sel.dataset.searchReady='1';const box=document.createElement('input');box.type='search';box.className='select-filter';box.placeholder='بحث في الاختيارات المسجلة';box.setAttribute('aria-label','بحث في '+(sel.closest('.field')?.querySelector('label')?.textContent||'القائمة'));sel.before(box);box.addEventListener('input',()=>{const q=box.value.trim().toLocaleLowerCase();for(const o of sel.options)o.hidden=!!q&&!o.text.toLocaleLowerCase().includes(q)&&!o.selected});
  }
 });
 let registered=document.getElementById('purchase-registered-names');if(!registered){registered=document.createElement('datalist');registered.id='purchase-registered-names';document.body.append(registered)}const names=typeof itemNames==='undefined'?[]:itemNames;const optionText=names.map(n=>'<option value="'+esc(n)+'">').join('');if(registered.innerHTML!==optionText)registered.innerHTML=optionText;
 NixtColorPicker.enhance();
 document.querySelectorAll('main>section:not(#tab-production),#tab-invoice').forEach(sec=>{
  let bar=sec.querySelector(':scope>.page-actionbar');if(!bar){bar=document.createElement('div');bar.className='page-actionbar';sec.prepend(bar)}
  sec.querySelectorAll('.page-actionbar button').forEach(p=>{if(p._source&&!p._source.isConnected)p.remove()});
  const candidates=[...sec.querySelectorAll('button')].filter(b=>!b.closest('.page-actionbar,.sole-color-tiles,table,.subnav,.quick-actions,.hero-tools,.dashboard-hero,.pm-pack,#se-chips,#pm-grid')&&!b.hasAttribute('data-no-topbar'));
  for(const b of candidates){
   if(b.dataset.topAction)continue;
   b.dataset.topAction='1';const proxy=document.createElement('button');proxy.className=b.className;proxy.innerHTML=b.innerHTML;proxy.title=b.title;if(/saveInvoice/.test(b.getAttribute('onclick')||''))proxy.dataset.linkedAction='1';proxy.addEventListener('click',()=>b.click());bar.append(proxy);b.classList.add('top-action-source');b._topProxy=proxy;proxy._source=b;
  }
  for(const b of sec.querySelectorAll('.top-action-source')){
   const p=b._topProxy;if(!p)continue;
   p.disabled=b.disabled;p.innerHTML=b.innerHTML;p.title=b.title;
   let visible=!b.classList.contains('hidden')&&b.style.display!=='none';for(let x=b.parentElement;x&&x!==sec;x=x.parentElement){if(x.classList.contains('hidden')||getComputedStyle(x).display==='none'){visible=false;break}}
   if(b.closest('.movement-intro'))visible=true;p.hidden=!visible;
  }
  bar.hidden=![...bar.children].some(b=>!b.hidden);
  enhanceRecordActions(sec,bar);
 });
 document.querySelectorAll('.modal').forEach(modal=>{
  if(modal.closest('#prod-modal,#invoice-entry-overlay,#shared-color-dialog'))return;
  if(modal.dataset.actionsReady){for(const p of modal.querySelectorAll('.modal-actionbar button')){const b=p._source;if(!b||!b.isConnected){p.remove();continue}p.disabled=b.disabled;p.innerHTML=b.innerHTML;p.hidden=b.hidden||b.classList.contains('hidden')||b.style.display==='none'}return;}
  const buttons=[...modal.querySelectorAll('button')].filter(b=>!b.closest('table,.entry-top,.color-picker,.sole-color-tiles')&&!/closest\(|\.remove\(|pmAddPack|ask|toggle|pin/.test(b.getAttribute('onclick')||''));
  if(!buttons.length)return;modal.dataset.actionsReady='1';const bar=document.createElement('div');bar.className='modal-actionbar';modal.prepend(bar);
  for(const b of buttons){const proxy=document.createElement('button');proxy.className=b.className;proxy.innerHTML=b.innerHTML;proxy.disabled=b.disabled;proxy._source=b;proxy.addEventListener('click',()=>b.click());bar.append(proxy);b.classList.add('top-action-source')}
 });
}
const uiObserver=new MutationObserver(()=>{if(uiRefreshPending)return;uiRefreshPending=true;requestAnimationFrame(()=>{uiObserver.disconnect();try{enhanceRegisteredControls()}finally{uiRefreshPending=false;uiObserver.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','style','class']})}})});
document.addEventListener('DOMContentLoaded',()=>{enhanceRegisteredControls();uiObserver.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','style','class']})});

function enhanceRecordActions(sec,bar){
 if(sec.id==='tab-invoice'||sec.id==='tab-dash'||sec.id==='tab-settings'||sec.id==='tab-production')return;
 sec.querySelectorAll('table tbody tr').forEach(row=>{
  const actions=[...row.querySelectorAll('button')].filter(b=>!b.closest('.color-picker')); if(!actions.length)return;
  row.classList.add('selectable-record');row.tabIndex=0;row.setAttribute('aria-label','تحديد السجل لعرض إجراءاته فوق');
  const choose=()=>{sec.querySelectorAll('.selected-record').forEach(r=>r.classList.remove('selected-record'));row.classList.add('selected-record');let tools=bar.querySelector('.record-actions');if(!tools){tools=document.createElement('span');tools.className='record-actions';bar.append(tools)}tools.replaceChildren();const caption=document.createElement('span');caption.className='muted';caption.textContent='السجل المحدد:';tools.append(caption);for(const b of actions){if(b.style.display==='none'||b.hidden)continue;const proxy=document.createElement('button');proxy.innerHTML=b.innerHTML;proxy.className=b.className;proxy.disabled=b.disabled;proxy._source=b;proxy.onclick=()=>b.click();tools.append(proxy)}};
  row.onclick=e=>{if(!e.target.closest('a,input,select,button'))choose()};row.onkeydown=e=>{if(e.target===row&&(e.key==='Enter'||e.key===' ')){e.preventDefault();choose()}};
 });
 let tip=sec.querySelector(':scope>.record-action-hint');if(!tip&&sec.querySelector('.selectable-record')){tip=document.createElement('div');tip.className='record-action-hint';tip.textContent='اضغط على أي سجل لعرض إجراءاته في الشريط العلوي';bar.after(tip)}
 for(const p of bar.querySelectorAll('.record-actions button')){const b=p._source;if(!b||!b.isConnected){p.remove();continue}p.disabled=b.disabled;p.innerHTML=b.innerHTML;p.hidden=b.hidden||b.classList.contains('hidden')||b.style.display==='none'}
 const chosen=sec.querySelector('.selected-record');if(!chosen)bar.querySelector('.record-actions')?.remove();
 }
