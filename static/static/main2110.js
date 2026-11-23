let customers = [];   // all parties
let currentUser = null;
let partyKind = 'customer';
let partyModalKind = null;
function openModal(id){ document.getElementById(id).style.display='flex'; }
function closeModal(id){ document.getElementById(id).style.display='none'; }
async function openPartyModal(kind){
  partyModalKind = kind||partyKind;
  resetCustomerForm();
  try{ const nc=await api('/api/parties/next-code?kind='+partyModalKind); if(!v('c-id')&&!v('c-code')) document.getElementById('c-code').value=nc.code; }catch(e){}
  document.getElementById('cust-form-title').textContent = partyModalKind==='supplier'?'إضافة مورد جديد':'إضافة عميل جديد';
  document.querySelectorAll('#party-modal .cust-only').forEach(el=>el.style.display=(partyModalKind==='customer')?'':'none');
  openModal('party-modal');
}
const fmt = n => (Number(n)||0).toLocaleString('en-US',{maximumFractionDigits:2});
const today = () => new Date().toISOString().slice(0,10);
const v = id => document.getElementById(id).value.trim();
const esc = s => String(s??'').replace(/[&<>"]/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
const KIND_LABEL = {sale:'فاتورة بيع',return:'مرتجع بيع',purchase:'فاتورة شراء',purchase_return:'مرتجع شراء'};
const KIND_PARTY = {sale:'customer',return:'customer',purchase:'supplier',purchase_return:'supplier'};
function toast(msg, bad){
  const t=document.getElementById('toast');
  t.textContent=msg;t.setAttribute('role',bad?'alert':'status');t.classList.toggle('error',!!bad);t.style.background='';t.style.display='block';
  setTimeout(()=>t.style.display='none',3500);
}
let uiRequests=0;
async function api(path, opts){
 uiRequests++;document.documentElement.classList.add("is-loading");
 try{
  const r = await fetch(path, opts);
  if(r.status===423){
    let j={}; try{ j=await r.clone().json(); }catch(_){}
    showLockBanner(j.locked_by||'', j.locked_ts||'');
    throw new Error('البرنامج مفتوح على جهاز تاني ('+(j.locked_by||'')+') - التعديلات مقفولة هنا');
  }
  if(r.status===401){
    let msg='login';
    try{ const j=await r.clone().json(); if(j && j.error) msg=j.error; }catch(_){}
    if(currentUser){ showLogin(); throw new Error('login'); }
    throw new Error(msg);
  }
  const j = await r.json();
  if(!r.ok){ const er=new Error(j.error||'خطأ'); er.need_approval=!!j.need_approval; er.limit=j.limit; throw er; }
  return j;
 }finally{uiRequests--;if(!uiRequests)document.documentElement.classList.remove("is-loading");}
}
function filterTable(tid, q){
  q=q.trim();
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{
    tr.style.display = !q || tr.textContent.includes(q) ? '' : 'none';
  });
}

/* ---------- auth ---------- */
function showLogin(){
  document.body.classList.add('signed-out');
  document.getElementById('login-screen').style.display='flex';
  document.getElementById('app-header').classList.add('hidden');
  document.getElementById('sidebar').classList.add('hidden');
  document.getElementById('app-main').classList.add('hidden');
  for(const x of ['accounts','inventory','production','settings'])
    document.getElementById('subnav-'+x).classList.add('hidden');
}
function roleLabel(r){ return r==='admin'?'مدير':r==='accountant'?'محاسب':r==='warehouse'?'مخزن':'مستخدم'; }
function hideSplash(){
  const s=document.getElementById('splash');
  if(!s) return;
  s.style.opacity='0';
  setTimeout(()=>{ if(s.parentNode) s.parentNode.removeChild(s); }, 400);
}
setTimeout(hideSplash, 9000);
function showApp(){
  document.body.classList.remove('signed-out');
  hideSplash();
  document.getElementById('login-screen').style.display='none';
  document.getElementById('app-header').classList.remove('hidden');
  document.getElementById('sidebar').classList.remove('hidden');
  document.getElementById('app-main').classList.remove('hidden');
  themeIcon();
  refreshBackupStatus();refreshNotices();
  initTopSearch();
  setTimeout(initSortable, 300);
  const role = currentUser && currentUser.role;
  const admin = role==='admin';
  document.querySelectorAll('.admin-only').forEach(b=>b.classList.toggle('hidden',!admin));
  if(role==='accountant'){
    for(const sec of ['inventory','production','settings']){
      const b=document.querySelector('#nav button[data-sec="'+sec+'"]');
      if(b) b.classList.add('hidden');
    }
  }
  if(role==='warehouse'){
    for(const sec of ['accounts','settings']){
      const b=document.querySelector('#nav button[data-sec="'+sec+'"]');
      if(b) b.classList.add('hidden');
    }
  }
  document.getElementById('who').textContent = currentUser.username + ' ('+roleLabel(role)+')';
  applyPerms();
  document.querySelectorAll('.side-secondary button,.quick-actions button,.hero-tools button').forEach(b=>{
   const code=b.getAttribute('onclick')||'';
   const section=code.includes('inventory')?'inventory':code.includes('settings')?'settings':'accounts';
   const allowed=role==='admin'||((role!=='warehouse'||section==='inventory')&&(role!=='accountant'||section==='accounts')&&(!currentUser.perms?.sections||currentUser.perms.sections.includes(section)));
   b.classList.toggle('hidden',!allowed);
  });
}
function applyPerms(){
  const p = currentUser && currentUser.perms;
  document.body.classList.toggle('no-del', !!(p && p.ops && p.ops.indexOf('delete_docs')>=0));
  if(p && p.sections){
    document.querySelectorAll('#nav button').forEach(b=>{
      if(p.sections.indexOf(b.dataset.sec)<0) b.classList.add('hidden');
    });
  }
}
/* ---------- 1.9.0: theme, sortable tables, top search ---------- */
function themeIcon(){
  const d = document.documentElement.dataset.theme === 'dark';
  const b = document.getElementById('theme-toggle');
  if(b) b.innerHTML = '<svg class="ic"><use href="#ic-' + (d ? 'sun' : 'moon') + '"/></svg>';
}
function toggleTheme(){
  const h = document.documentElement;
  h.dataset.theme = h.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('nixt-premium-theme', h.dataset.theme);
  themeIcon();
}
function makeSortable(table){
  const heads = table.querySelectorAll('thead th');
  heads.forEach((th, idx) => {
    if(th.classList.contains('sortable') || th.textContent.trim() === '' || th.querySelector('input,select,button')) return;
    th.classList.add('sortable');
    th.addEventListener('click', () => {
      const tb = table.tBodies[0];
      if(!tb) return;
      const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      heads.forEach(h => { delete h.dataset.dir; const s = h.querySelector('.dir'); if(s) s.remove(); });
      th.dataset.dir = dir;
      const mark = document.createElement('span');
      mark.className = 'dir';
      mark.textContent = dir === 'asc' ? '\u25B2' : '\u25BC';
      th.appendChild(mark);
      const rows = [...tb.rows];
      rows.sort((a, b) => {
        const x = (a.cells[idx] ? a.cells[idx].textContent : '').trim();
        const y = (b.cells[idx] ? b.cells[idx].textContent : '').trim();
        const nx = parseFloat(x.replace(/[^0-9.\-]/g, ''));
        const ny = parseFloat(y.replace(/[^0-9.\-]/g, ''));
        let c;
        if(!isNaN(nx) && !isNaN(ny) && x !== '' && y !== '') c = nx - ny;
        else c = x.localeCompare(y, 'ar');
        return dir === 'asc' ? c : -c;
      });
      rows.forEach(r => tb.appendChild(r));
    });
  });
}
function initSortable(){
  document.querySelectorAll('main table').forEach(t => { if(!t.classList.contains('items-table')) makeSortable(t); });
}
function initTopSearch(){
  const ts = document.getElementById('top-search');
  if(!ts) return;
  ts.addEventListener('input', () => {
    const sec = document.querySelector('main section:not(.hidden)');
    if(!sec) return;
    const local = sec.querySelector('.search');
    if(local && local !== ts){
      local.value = ts.value;
      local.dispatchEvent(new Event('input'));
    }
  });
}

function showLockBanner(dev, ts){
  document.getElementById('lock-dev').textContent = dev + (ts ? ' (من '+ts+')' : '');
  document.getElementById('lock-banner').classList.remove('hidden');
}
function hideLockBanner(){
  document.getElementById('lock-banner').classList.add('hidden');
}
async function refreshLock(){
  try{
    const me = await api('/api/me');
    if(me.locked_by){ showLockBanner(me.locked_by, me.locked_ts); toast('لسه مفتوح على '+me.locked_by, true); }
    else { hideLockBanner(); toast('اتفتح - الجهاز ده بقى هو النشط'); }
  }catch(e){toast(e.message,true);}
}
async function forceLock(){
  if(!confirm('استخدم ده لو بس متأكد إن البرنامج مقفول على الجهاز التاني. لو مفتوح هناك فعلاً، التعديلات ممكن تتلخبط. متأكد؟')) return;
  try{
    await api('/api/sync/force-lock',{method:'POST',body:'{}'});
    hideLockBanner();
    toast('اتفتح على الجهاز ده');
  }catch(e){toast(e.message,true);}
}
async function bootAuth(){
  const me = await api('/api/me');
  document.getElementById('app-ver').textContent='إصدار '+me.version;
  document.getElementById('login-ver').textContent='إصدار '+me.version;
  if(me.locked_by){ showLockBanner(me.locked_by, me.locked_ts); } else { hideLockBanner(); }
  if(me.needs_setup){
    hideSplash();
    document.getElementById('setup-form').classList.remove('hidden');
    return;
  }
  if(!me.logged_in){
    hideSplash();
    document.getElementById('login-form').classList.remove('hidden');
    loadLoginUsers();
    return;
  }
  currentUser = {username:me.username, role:me.role, must_change:!!me.must_change, has_pin:!!me.has_pin, perms:me.perms||null, idle_min:me.idle_lock_minutes||0};
  showApp();
  startIdleWatch();
  if(currentUser.must_change){ openModal('mustchange-modal'); }
}
async function doSetup(){
  const u=v('su-username'), p1=document.getElementById('su-password').value, p2=document.getElementById('su-password2').value;
  if(p1!==p2){ document.getElementById('login-err').textContent='كلمتا السر غير متطابقتين'; return; }
  try{
    const r=await api('/api/setup',{method:'POST',body:JSON.stringify({username:u,password:p1})});
    currentUser={username:u,role:'admin'}; showApp(); bootData();
    const comp=(document.getElementById('su-company').value||'').trim();
    if(comp){ try{ await api('/api/settings',{method:'POST',body:JSON.stringify({company_name:comp})}); }catch(e){} }
    setTimeout(showGuide,600);
  }catch(e){ document.getElementById('login-err').textContent=e.message; }
}
function fmtLast(ts){
  if(!ts) return '';
  const d=ts.slice(0,10), t=ts.slice(11,16);
  const n=new Date();
  const td=n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0');
  return 'آخر دخول: '+(d===td?'النهارده '+t:d);
}
let LOGIN_USERS=[];
async function loadLoginUsers(){
  try{
    const users = await api('/api/login_users');
    const box = document.getElementById('login-users');
    if(!users || !users.length){ return; }
    LOGIN_USERS = users.slice().sort((a,b)=>(b.last_login||'').localeCompare(a.last_login||''));
    let recent=[]; try{ recent=JSON.parse(localStorage.getItem('nixt-recent-users')||'[]'); }catch(e){}
    const names=LOGIN_USERS.map(u=>u.username);
    recent=recent.filter(n=>names.indexOf(n)>=0);
    LOGIN_USERS.forEach(u=>{ if(recent.indexOf(u.username)<0 && recent.length<4) recent.push(u.username); });
    const top=recent.slice(0,4).map(n=>LOGIN_USERS.find(u=>u.username===n)).filter(Boolean);
    box.innerHTML='';
    top.forEach(u=>{
      const d=document.createElement('div');
      d.className='user-chip';
      d.innerHTML='<div class="avatar">'+esc(u.initials)+'</div><div class="uname">'+esc(u.username)+'</div><div class="urole">'+roleLabel(u.role)+'</div>'+(u.last_login?'<div class="ulast">'+esc(fmtLast(u.last_login))+'</div>':'');
      d.onclick=()=>pickUser(u.username, d);
      box.appendChild(d);
    });
    box.classList.remove('hidden');
    document.getElementById('login-all-wrap').classList.remove('hidden');
    renderAllUsers();
    document.getElementById('li-username').classList.add('hidden');
    document.getElementById('li-other').classList.remove('hidden');
    const rem = localStorage.getItem('nixt-remember-user')==='1';
    document.getElementById('li-remember').checked = rem;
    const last = localStorage.getItem('nixt-last-user')||'';
    if(rem && last){
      const chip=[...box.children].find(c=>c.querySelector('.uname').textContent===last);
      if(chip) chip.click();
    }
  }catch(e){ /* keep manual form */ }
}
function toggleAllUsers(){
  const w=document.getElementById('login-all');
  const opening=w.classList.contains('hidden');
  w.classList.toggle('hidden');
  document.getElementById('li-all-toggle').textContent=opening?'إخفاء القائمة ▴':'كل المستخدمين ▾';
  if(opening){ renderAllUsers(); document.getElementById('li-search').focus(); }
}
function renderAllUsers(){
  const si=document.getElementById('li-search');
  const q=(si.value||'').trim();
  const list=document.getElementById('login-all-list');
  const rows=(LOGIN_USERS||[]).filter(u=>!q||u.username.includes(q));
  list.innerHTML=rows.length?rows.map(u=>'<div class="urow" data-u="'+esc(u.username)+'" onclick="pickUserFromList(this.dataset.u)"><div class="avatar">'+esc(u.initials)+'</div><div class="urow-m"><div class="uname">'+esc(u.username)+'</div><div class="ulast">'+esc(roleLabel(u.role))+(u.last_login?' · '+esc(fmtLast(u.last_login)):'')+'</div></div></div>').join(''):'<div class="muted" style="padding:8px;font-size:13px">مفيش مستخدم بالاسم ده</div>';
}
function pickUserFromList(name){
  const un=document.getElementById('li-username'), pk=document.getElementById('li-picked');
  un.value=name; un.classList.add('hidden');
  pk.textContent=name; pk.classList.remove('hidden');
  document.querySelectorAll('.user-chip').forEach(c=>c.classList.remove('sel'));
  document.getElementById('li-password').focus();
  document.getElementById('login-err').textContent='';
}
function showGuide(){ const g=document.getElementById('guide-overlay'); if(g) g.classList.remove('hidden'); }
function closeGuide(){ const g=document.getElementById('guide-overlay'); if(g) g.classList.add('hidden'); try{localStorage.setItem('nixt_guide_seen','1');}catch(e){} }
function pickUser(name, el){
  document.querySelectorAll('.user-chip').forEach(c=>c.classList.remove('sel'));
  const un=document.getElementById('li-username'), pk=document.getElementById('li-picked');
  if(name){
    el.classList.add('sel');
    un.value=name; un.classList.add('hidden');
    pk.textContent=name; pk.classList.remove('hidden');
    document.getElementById('li-password').focus();
  }else{
    un.value=''; un.classList.remove('hidden');
    pk.classList.add('hidden');
    document.getElementById('login-users').classList.add('hidden');
    document.getElementById('li-other').classList.add('hidden');
    un.focus();
  }
  document.getElementById('login-err').textContent='';
}
let loginLockTimer=null;
async function doLogin(){
  const err=document.getElementById('login-err');
  err.textContent='';
  const btn=document.getElementById('li-btn');
  btn.disabled=true;
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:v('li-username'),password:document.getElementById('li-password').value})});
    const j=await r.json().catch(()=>({}));
    if(r.status===429 && j.locked){ startLoginCountdown(j.wait||300, j.error); return; }
    if(!r.ok){ err.textContent=j.error||'بيانات الدخول غير صحيحة'; return; }
    const rem=document.getElementById('li-remember').checked;
    localStorage.setItem('nixt-remember-user', rem?'1':'0');
    if(rem) localStorage.setItem('nixt-last-user', j.username);
    try{ let rec=JSON.parse(localStorage.getItem('nixt-recent-users')||'[]'); rec=[j.username].concat(rec.filter(n=>n!==j.username)).slice(0,8); localStorage.setItem('nixt-recent-users',JSON.stringify(rec)); }catch(e){}
    currentUser={username:j.username, role:j.role, must_change:!!j.must_change, has_pin:!!j.has_pin, perms:null, idle_min:0};
    showApp(); bootData(); startIdleWatch();
    if(currentUser.must_change) openModal('mustchange-modal');
  }catch(e){ err.textContent='مش قادر أوصل للبرنامج - جرب تاني'; }
  finally{ btn.disabled=false; }
}
function startLoginCountdown(wait, msg){
  const err=document.getElementById('login-err');
  const btn=document.getElementById('li-btn');
  btn.disabled=true;
  let left=Math.max(1,wait|0);
  clearInterval(loginLockTimer);
  const tick=()=>{
    const m=Math.floor(left/60), s=left%60;
    err.textContent=(msg||'الحساب متقفل مؤقتًا')+' - فاضل '+m+':'+String(s).padStart(2,'0');
    if(left<=0){ clearInterval(loginLockTimer); btn.disabled=false; err.textContent=''; }
    left--;
  };
  tick(); loginLockTimer=setInterval(tick,1000);
}
function togglePw(inputId, btnId){
  const i=document.getElementById(inputId), b=document.getElementById(btnId);
  const show=i.type==='password';
  i.type=show?'text':'password';
  b.innerHTML='<svg class="ic"><use href="#ic-'+(show?'eye-off':'eye')+'"/></svg>';
}
let idleTimer=null, lockedNow=false, lastIdleReset=0;
function startIdleWatch(){
  clearTimeout(idleTimer);
  const mins=(currentUser&&currentUser.idle_min)||0;
  if(!mins || lockedNow) return;
  idleTimer=setTimeout(lockScreen, mins*60*1000);
}
['click','keydown','mousemove'].forEach(ev=>document.addEventListener(ev,()=>{
  const n=Date.now();
  if(currentUser && !lockedNow && n-lastIdleReset>3000){ lastIdleReset=n; startIdleWatch(); }
},{passive:true}));
function lockScreen(){
  if(!currentUser || lockedNow) return;
  document.getElementById('notice-panel').classList.add('hidden');
  lockedNow=true;
  clearTimeout(idleTimer);
  document.getElementById('lock-who').textContent='البرنامج متقفل - '+currentUser.username;
  document.getElementById('lock-password').value='';
  document.getElementById('lock-err').textContent='';
  document.getElementById('lock-overlay').classList.remove('hidden');
  setTimeout(()=>document.getElementById('lock-password').focus(),100);
}
async function doUnlock(){
  try{
    await api('/api/unlock',{method:'POST',body:JSON.stringify({password:document.getElementById('lock-password').value})});
    lockedNow=false;
    document.getElementById('lock-overlay').classList.add('hidden');
    startIdleWatch();
  }catch(e){ document.getElementById('lock-err').textContent=e.message; }
}
async function doMustChange(){
  const p0=document.getElementById('mc-pass0').value, p1=document.getElementById('mc-pass1').value, p2=document.getElementById('mc-pass2').value;
  const err=document.getElementById('mc-err');
  if(p1.length<4){ err.textContent='كلمة السر الجديدة 4 أحرف على الأقل'; return; }
  if(p1!==p2){ err.textContent='كلمتا السر غير متطابقتين'; return; }
  try{
    await api('/api/change-password',{method:'POST',body:JSON.stringify({old:p0,new:p1})});
    currentUser.must_change=false;
    closeModal('mustchange-modal');
    toast('تم تغيير كلمة السر - أهلا بيك');
  }catch(e){ err.textContent=e.message; }
}
async function doLogout(){
  noticeItems=[];noticeUpdate=null;noticeOwner=null;noticeUpdateCheck=0;document.getElementById('notice-panel').classList.add('hidden');renderNotices();
  await api('/api/logout',{method:'POST',body:'{}'});
  location.reload();
}

/* ---------- nav ---------- */
const TAB_SEC = {dash:'home',accounts:'accounts','item-monthly':'accounts',invoice:'accounts',sales:'accounts',purchases:'accounts',payments:'accounts',statement:'accounts',treasury:'accounts',cheques:'accounts',quotes:'accounts',profit:'accounts',inventory:'inventory',production:'production',settings:'settings',import:'settings',defs:'settings',sync:'settings'};
const INV_TITLES = {materials:'المخزن - الخامات',soles:'المخزن - النعال',leather:'المخزن - الجلد (قدم مربع)',ready:'المخزن - المخزون الجاهز',orders:'الإنتاج - أوامر الإنتاج',models:'الإنتاج - الموديلات'};
function showSection(sec){
  if(currentUser && currentUser.role==='accountant' && sec!=='accounts' && sec!=='home'){
    toast('صلاحيتك على الحسابات بس', true); return;
  }
  if(currentUser && currentUser.role==='warehouse' && sec!=='inventory' && sec!=='production'){
    toast('صلاحيتك على المخزن والإنتاج بس', true); return;
  }
  const _permSec = TAB_SEC[sec]||sec;
  if(sec!=='home' && currentUser && currentUser.perms && currentUser.perms.sections && currentUser.perms.sections.indexOf(_permSec)<0){
    toast('القسم ده مش متاح ليك', true); return;
  }
  showTab(sec==='settings'?'settings':sec==='inventory'?'inventory':sec==='production'?'production':sec==='accounts'?'sales':'dash');
}
const pageTitles={dash:'لوحة المتابعة',sales:'المبيعات',purchases:'المشتريات',accounts:'العملاء والموردين',quotes:'عروض الأسعار',invoice:'الفواتير',payments:'الدفعات والتحصيل',treasury:'الخزينة',cheques:'إدارة الشيكات',collections:'متابعة التحصيل',reports:'التقارير والتحليل',profit:'الأرباح',statement:'كشف الحساب','item-monthly':'مبيعات صنف شهرياً',inventory:'إدارة المخزون',production:'الإنتاج',settings:'إعدادات النظام',defs:'البيانات الأساسية',sync:'المزامنة',import:'استيراد البيانات'};
function showTab(t, kind){
  document.body.dataset.activeTab=t;
  const crumb=document.getElementById('workspace-title');if(crumb)crumb.textContent=pageTitles[t]||'مساحة العمل';
  document.querySelectorAll('main > section').forEach(s=>s.classList.add('hidden'));
  document.getElementById('tab-'+t).classList.remove('hidden');
  const sec = TAB_SEC[t]||'accounts';
  if(sec==='accounts' && TAB_GROUP[t]) showAccGroup(TAB_GROUP[t]);
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('active', b.dataset.sec===sec));
  for(const x of ['accounts','inventory','production','settings'])
    document.getElementById('subnav-'+x).classList.toggle('hidden', sec!==x);
  if(t==='inventory' || t==='production'){
    const sub = kind || (t==='inventory'?'materials':'orders');
    document.querySelectorAll('#subnav-'+t+' button').forEach(b=>b.classList.toggle('active', b.dataset.inv===sub));
    const ttl = document.getElementById(t==='inventory'?'inv-title':'prod-title');
    if(ttl) ttl.textContent = INV_TITLES[sub]||ttl.textContent;
  }
  if(t==='accounts' && kind) partyKind = kind;
  document.querySelectorAll('#subnav-accounts .subnav-items button').forEach(b=>b.classList.toggle('active',
    b.dataset.tab===t && (t!=='accounts' || b.dataset.kind===partyKind)));
  document.querySelectorAll('#subnav-settings button').forEach(b=>b.classList.toggle('active', b.dataset.tab===t));
  if(t==='inventory') loadStock();
  if(t==='production') loadProduction();
  if(t==='dash') loadDash();
  if(t==='accounts') setPartyKind(partyKind);
  if(t==='invoice') loadInvoices();
  if(t==='sales') loadMovements('sale');
  if(t==='purchases') loadMovements('purchase');
  if(t==='payments') loadPayments();
  if(t==='treasury') loadTreasury();
  if(t==='cheques') loadCheques();
  if(t==='collections') loadCollections();
  if(t==='quotes') loadQuotes();
  if(t==='reports'){
    if(!document.getElementById('mc-month').value) document.getElementById('mc-month').value=today().slice(0,7);
    loadTopItems();
    loadModelProfit();
  }
  if(t==='profit'){
    if(!document.getElementById('pr-month').value) document.getElementById('pr-month').value=today().slice(0,7);
    loadProfit();
  }
  if(t==='settings') loadSettings();
  if(t==='defs'){ loadEmployees(); loadItems(); }
  if(t==='sync') loadSync();
}
function setPartyKind(k){
  partyKind=k;
  document.querySelectorAll('#subnav-accounts .subnav-items button[data-tab="accounts"]').forEach(b=>b.classList.toggle('active', b.dataset.kind===k));
  document.querySelectorAll('#tab-accounts .cust-only').forEach(el=>el.style.display=(k==='customer')?'':'none');
  const ab=document.getElementById('party-add-btn'); if(ab) ab.textContent = k==='supplier'?'+ إضافة مورد':'+ إضافة عميل';
  partyModalKind = null;
  resetCustomerForm();
  loadCustomers();
  fillPriceCustomers();
}

/* ---------- shared ---------- */
async function refreshCustomers(){
  customers = await api('/api/customers');
  const mk = list => list.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const all = '<option value="">-- اختر --</option>'+mk(customers);
  for(const id of ['p-customer','s-customer']){
    const el=document.getElementById(id); const vv=el.value;
    el.innerHTML=all; el.value=vv;
  }
  fillInvoiceParties();
}
function fillInvoiceParties(){
  const kind = KIND_PARTY[document.getElementById('i-kind').value] || 'customer';
  const list = customers.filter(c=>(c.kind||'customer')===kind);
  const el=document.getElementById('i-customer'); const vv=el.value;
  el.innerHTML='<option value="">-- اختر --</option>'+list.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  el.value=vv;
  document.getElementById('i-party-label').textContent = kind==='supplier'?'المورد *':'العميل *';
}
function onInvoiceKind(){
  const supplier=!invIsCustomer();
  document.getElementById('purchase-material-entry').classList.toggle('hidden',!supplier);
  document.getElementById('sale-item-entry').classList.toggle('hidden',supplier);
  document.getElementById('invoice-manual-add').classList.toggle('hidden',supplier);
  pmRender();
  renderItemsHead();
  fillInvoiceParties(); onInvoiceCustomer(); calcInvoice();
  const k=document.getElementById('i-kind').value;
  document.getElementById('i-reason-field').classList.toggle('hidden', !(k==='return'||k==='purchase_return'));
}

/* ---------- 1.9.0 v2: icon helper, grouped subnav, charts ---------- */
function ic(name){ return '<svg class="ic"><use href="#ic-' + name + '"/></svg>'; }
const TAB_GROUP = {accounts:'parties',invoice:'docs',statement:'analysis',collections:'money',
  sales:'docs',purchases:'docs',quotes:'docs',payments:'money',treasury:'money',cheques:'money',
  profit:'analysis',reports:'analysis','item-monthly':'analysis'};
function showAccGroup(g){
  document.querySelectorAll('#accounts-groups button').forEach(b=>b.classList.toggle('active', b.dataset.group===g));
  document.querySelectorAll('#subnav-accounts .subnav-items button').forEach(b=>{
    b.style.display = b.dataset.group===g ? '' : 'none';
  });
}
const AR_MONTHS=['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'];
function renderDashCharts(d){
  const ms = d.monthly_sales || [];
  const W=340, H=180, pad=16;
  const slot=(W-pad*2)/Math.max(ms.length,1);
  const bw=Math.min(36, slot-10);
  const max=Math.max(...ms.map(m=>m.total), 1);
  let bars='';
  ms.forEach((m,i)=>{
    const bh=(H-56)*(m.total/max);
    const x=pad+i*slot+(slot-bw)/2;
    const y=H-32-bh;
    bars+='<rect class="bar'+(i===ms.length-1?' last':'')+'" x="'+x+'" y="'+y+'" width="'+bw+'" height="'+Math.max(bh,0)+'"><title>'+fmt(m.total)+'</title></rect>'
      +'<text x="'+(x+bw/2)+'" y="'+(H-16)+'" text-anchor="middle">'+AR_MONTHS[+m.month.slice(5,7)-1].slice(0,6)+'</text>'
      +(m.total?'<text x="'+(x+bw/2)+'" y="'+(y-6)+'" text-anchor="middle" style="font-weight:700;fill:var(--text)">'+fmt(Math.round(m.total))+'</text>':'');
  });
  document.getElementById('ch-monthly').innerHTML='<svg class="chart-svg" viewBox="0 0 '+W+' '+H+'">'+bars+'</svg>';
  const paid=d.customer.paid, rem=Math.max(d.customer.remaining,0), tot=paid+rem||1;
  const frac=paid/tot, R=52, C=2*Math.PI*R;
  document.getElementById('ch-donut').innerHTML='<svg class="chart-svg" viewBox="0 0 340 150">'
    +'<g transform="translate(85,75)">'
    +'<circle r="'+R+'" fill="none" stroke="var(--line)" stroke-width="20"/>'
    +'<circle r="'+R+'" fill="none" stroke="var(--accent)" stroke-width="20" stroke-linecap="round" stroke-dasharray="'+(C*frac).toFixed(1)+' '+C.toFixed(1)+'" transform="rotate(-90)"/>'
    +'<text class="donut-val" text-anchor="middle" dy="7">'+Math.round(frac*100)+'%</text></g>'
    +'<text x="170" y="60">المحصل من العملاء</text>'
    +'<text x="170" y="80" style="font-weight:800;font-size:16px;fill:var(--accent2)">'+fmt(paid)+' ج</text>'
    +'<text x="170" y="108">المتبقي عليهم</text>'
    +'<text x="170" y="128" style="font-weight:800;font-size:16px;fill:var(--text)">'+fmt(rem)+' ج</text>'
    +'</svg>'
    +'<div class="legend"><span><span class="dot" style="background:var(--accent)"></span>المحصل</span><span><span class="dot" style="background:var(--line)"></span>المتبقي</span></div>';
  const tm=d.top_models||[];
  const tmax=Math.max(...tm.map(x=>x.pairs),1);
  document.getElementById('ch-top').innerHTML = tm.length
    ? '<svg class="chart-svg" viewBox="0 0 340 '+(tm.length*36+6)+'">'+tm.map((x,i)=>{
        const w=Math.max(8, 296*(x.pairs/tmax));
        const y=i*36+4;
        return '<text x="334" y="'+(y+11)+'" text-anchor="end" style="fill:var(--text);font-weight:600">'+esc(x.name)+'</text>'
          +'<rect class="hbar" x="'+(334-w)+'" y="'+(y+17)+'" width="'+w+'" height="12" rx="6"><title>'+fmt(x.pairs)+' زوج</title></rect>'
          +'<text x="'+(328-w)+'" y="'+(y+27)+'" text-anchor="end" style="font-size:10.5px">'+fmt(x.pairs)+'</text>';
      }).join('')+'</svg>'
    : '<span class="muted">لا توجد مبيعات بعد.</span>';
}

/* ---------- dashboard ---------- */
async function loadUndo(){
  const p=document.getElementById('undo-panel');
  if(!currentUser || currentUser.role!=='admin'){ if(p) p.style.display='none'; return; }
  try{
    const [invs,pays]=await Promise.all([api('/api/invoices'),api('/api/payments')]);
    let last=null;
    for(const i of invs){ if(!last || (i.created_at||'')>last.created_at) last={type:'invoice',id:i.id,created_at:i.created_at||'',label:(KIND_LABEL[i.kind]||i.kind)+' '+(i.number||('#'+i.id))+' - '+i.customer_name+' - '+fmt(i.net)+' ج'}; }
    for(const y of pays){ if(!last || (y.created_at||'')>last.created_at) last={type:'payment',id:y.id,created_at:y.created_at||'',label:(y.party_kind==='supplier'?'دفعة لمورد':'قبض من عميل')+' - '+y.customer_name+' - '+fmt(y.total)+' ج'}; }
    if(!last){ p.style.display='none'; return; }
    p.style.display='';
    document.getElementById('undo-body').innerHTML='<span style="font-size:14px">'+esc(last.label)+'</span> <button class="small danger" style="margin-inline-start:10px" onclick="undoLast(\''+last.type+'\','+last.id+')">تراجع واحذف</button> <span class="muted" style="font-size:11px">('+esc((last.created_at||'').slice(0,16))+')</span>';
  }catch(e){}
}
async function undoLast(type,id){
  if(!confirm('حذف آخر عملية نهائيًا؟ هتتشال من الحسابات والسجل.')) return;
  try{
    await api('/api/'+(type==='invoice'?'invoices':'payments')+'/'+id,{method:'DELETE'});
    toast('تم التراجع عن آخر عملية');
    loadDash(); loadInvoices(); loadPayments(); refreshCustomers();
  }catch(e){toast(e.message,true);}
}
async function loadRecentInvoices(){
 const box=document.getElementById('recent-invoices');
 try{const list=(await api('/api/invoices')).slice(0,6);box.innerHTML='<table><thead><tr><th>رقم الفاتورة</th><th>التاريخ</th><th>النوع</th><th>العميل / المورد</th><th>القيمة</th><th></th></tr></thead><tbody>'+list.map(x=>'<tr><td class="num">'+esc(x.number||x.id)+'</td><td class="num">'+esc(x.date)+'</td><td><span class="type-pill type-'+esc(x.kind)+'">'+esc(KIND_LABEL[x.kind]||x.kind)+'</span></td><td>'+esc(x.customer_name)+'</td><td class="num"><b>'+fmt(x.net)+' ج</b></td><td><button class="small ghost" onclick="printInvoice('+Number(x.id)+')">عرض</button></td></tr>').join('')+'</tbody></table>';premiumTables();}catch(e){box.textContent='مش قادر أحمل الفواتير حالياً';}
}
async function loadDash(){
  loadToday(); loadUndo();
  const d = await api('/api/dashboard');
  const ms2 = d.monthly_sales || [];
  const cur = ms2.length ? ms2[ms2.length-1].total : 0;
  const prev = ms2.length > 1 ? ms2[ms2.length-2].total : 0;
  const pct = prev > 0 ? Math.round((cur-prev)/prev*100) : (cur > 0 ? 100 : 0);
  const chip = prev>0?`<span class="delta ${pct>=0?'up':'down'}">${pct>=0?'▲':'▼'} ${Math.abs(pct)}%</span>`:'';
  const kpis = [
    {t:'مبيعات الشهر', v:cur, icn:'receipt-text', dl:chip?chip+' <span class="muted" style="font-size:11px">عن الشهر اللي فات</span>':'<span class="muted" style="font-size:11px">الشهر الحالي</span>'},
    {t:'إجمالي المبيعات', v:d.customer.sales, icn:'chart-line'},
    {t:'المحصل من العملاء', v:d.customer.paid, icn:'wallet'},
    {t:'متبقي على العملاء', v:d.customer.remaining, icn:'users'},
    {t:'مشتريات', v:d.supplier.sales, icn:'shopping-cart'},
    {t:'مستحق للموردين', v:d.supplier.remaining, icn:'truck'},
    {t:'مرتجعات البيع', v:d.customer.returns, icn:'refresh-cw'},
    {t:'الأزواج المباعة', v:d.pairs, icn:'package'},
  ];
  document.getElementById('dash-cards').innerHTML = kpis.map(k=>
    `<div class="card kpi"><div class="kpi-ic">${ic(k.icn)}</div><div class="kpi-body"><div class="t">${k.t}</div><div class="v num">${fmt(k.v)} ${k.dl||''}</div></div></div>`).join('');
  renderDashCharts(d);loadRecentInvoices();
  const al=d.alerts||{over_limit:[],stale:[]};
  let ah='';
  al.over_limit.forEach(x=>{ah+=`<li class="bad"><svg class="ic"><use href="#ic-alert-triangle"/></svg> ${esc(x.name)} عدّى حد الائتمان: عليه <b class="num">${fmt(x.balance)}</b> والحد <span class="num">${fmt(x.limit)}</span> <button class="small" onclick="openStatement(${x.id})">كشف</button></li>`;});
  al.stale.forEach(x=>{ah+=`<li><svg class="ic"><use href="#ic-phone"/></svg> ${esc(x.name)} عليه <b class="num">${fmt(x.balance)}</b> وآخر حركة منذ <span class="num">${x.days}</span> يوم <button class="small" onclick="openStatement(${x.id})">كشف</button></li>`;});
  document.getElementById('dash-alerts').innerHTML = ah ? `<ul class="alert-list">${ah}</ul>` : '<span class="muted">لا توجد تنبيهات.</span>';
  const cq=d.cheques_due||[];
  const today0=today();
  document.getElementById('dash-cheques').innerHTML = cq.length
    ? '<h3 style="margin:8px 0 4px">شيكات مستحقة خلال 7 أيام</h3><ul class="alert-list">'+cq.map(c=>
      `<li class="${c.due_date<today0?'bad':''}"><svg class="ic"><use href="#ic-wallet"/></svg> ${esc(c.customer_name)} - شيك <b class="num">${fmt(c.amount)}</b> يستحق <span class="num">${esc(c.due_date)}</span>${c.due_date<today0?' (متأخر)':''}</li>`).join('')+'</ul>'
    : '';
  try{
    const ag=await api('/api/aging');
    const ac=ag.customers||[];
    document.getElementById('dash-aging').innerHTML = ac.length
      ? '<table><thead><tr><th>العميل</th><th>حتى 30 يوم</th><th>31-60</th><th>61-90</th><th>أكثر من 90</th><th>الإجمالي</th><th></th></tr></thead><tbody>'
        +ac.map(c=>`<tr><td>${esc(c.name)}</td><td class="num">${fmt(c.current)}</td><td class="num">${c.d30?fmt(c.d30):''}</td>
          <td class="num ${c.d60?'bad':''}">${c.d60?fmt(c.d60):''}</td><td class="num ${c.d90?'bad':''}">${c.d90?fmt(c.d90):''}</td>
          <td class="num"><b>${fmt(c.total)}</b></td>
          <td><button class="small" onclick="openStatement(${c.id})">كشف</button></td></tr>`).join('')
        +`</tbody><tfoot><tr><td><b>الإجمالي</b></td><td class="num"><b>${fmt(ag.totals.current)}</b></td><td class="num"><b>${fmt(ag.totals.d30)}</b></td><td class="num"><b>${fmt(ag.totals.d60)}</b></td><td class="num bad"><b>${fmt(ag.totals.d90)}</b></td><td class="num"><b>${fmt(ag.totals.total)}</b></td><td></td></tr></tfoot></table>`
      : '<span class="muted">لا توجد ديون قائمة.</span>';
  }catch(e){ document.getElementById('dash-aging').innerHTML=''; }
  const rows = d.debtors.map(x=>`<tr><td>${esc(x.name)}</td><td class="num bad">${fmt(x.balance)}</td>
    <td><button class="small" onclick="openStatement(${x.id})">كشف</button></td></tr>`).join('');
  document.getElementById('dash-debtors').innerHTML = d.debtors.length
    ? `<table><thead><tr><th>العميل</th><th>المتبقي عليه</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : '<span class="muted">لا يوجد مديونيات.</span>';
  const rows2 = d.creditors.map(x=>`<tr><td>${esc(x.name)}</td><td class="num bad">${fmt(x.balance)}</td>
    <td><button class="small" onclick="openStatement(${x.id})">كشف</button></td></tr>`).join('');
  document.getElementById('dash-creditors').innerHTML = d.creditors.length
    ? `<table><thead><tr><th>المورد</th><th>مستحق له</th><th></th></tr></thead><tbody>${rows2}</tbody></table>`
    : '<span class="muted">لا توجد مستحقات للموردين.</span>';
}

/* ---------- accounts (parties) ---------- */
async function loadCustomers(){
  await refreshCustomers();
  const list = customers.filter(c=>(c.kind||'customer')===partyKind);
  document.getElementById('cust-list-title').textContent = partyKind==='supplier'?'الموردون':'العملاء';
  const balLabel = partyKind==='supplier'?'مستحق له':'المتبقي عليه';
  document.querySelector('#cust-table thead tr').children[5].textContent = balLabel;
  document.querySelector('#cust-table tbody').innerHTML = list.map(c=>`<tr>
    <td class="num">${esc(c.code||'')}</td><td>${esc(c.name)}</td><td class="num">${esc(c.phone)}</td>
    <td>${esc(c.group_name)}</td><td class="num">${c.discount_pct||0}</td>
    <td class="num ${c.balance>0?'bad':'ok'}">${fmt(c.balance)}</td>
    <td style="white-space:nowrap">
      <button class="small" onclick="openStatement(${c.id})">كشف</button>
      <button class="small ghost" onclick="editCustomer(${c.id})">تعديل</button>
      <button class="small danger admin-only" ${currentUser.role!=='admin'?'style="display:none"':''} onclick="delCustomer(${c.id})">حذف</button>
    </td></tr>`).join('');
  filterTable('cust-table', document.getElementById('cust-search').value);
}
function resetCustomerForm(){
  for(const id of ['c-id','c-name','c-code','c-phone','c-address','c-group','c-disc','c-limit','c-opening','c-opening-date','c-notes'])
    document.getElementById(id).value='';
}
async function saveCustomer(){
  const id = v('c-id');
  const body = {name:v('c-name'), phone:v('c-phone'), group_name:v('c-group'), discount_pct:v('c-disc')||0,
    notes:v('c-notes'), kind:partyModalKind||partyKind, code:v('c-code'), address:v('c-address'),
    credit_limit:v('c-limit')||0, opening:v('c-opening')||0, opening_date:v('c-opening-date')};
  if(!body.name){ toast('اكتب الاسم', true); return; }
  try{
    let r;
    if(id) r = await api('/api/customers/'+id, {method:'POST', body:JSON.stringify(body)});
    else r = await api('/api/customers', {method:'POST', body:JSON.stringify(body)});
    toast('تم الحفظ'); closeModal('party-modal'); resetCustomerForm();
    await refreshCustomers(); loadCustomers();
    if(r && r.id && KIND_PARTY[document.getElementById('i-kind').value]===(partyModalKind||partyKind)){
      const sel=document.getElementById('i-customer'); sel.value=String(r.id); onInvoiceCustomer();
    }
  }catch(e){ toast(e.message, true); }
}
function editCustomer(id){
  const c = customers.find(x=>x.id===id); if(!c) return;
  document.getElementById('cust-form-title').textContent='تعديل بيانات';
  document.getElementById('c-id').value=c.id;
  document.getElementById('c-name').value=c.name;
  document.getElementById('c-code').value=c.code||'';
  document.getElementById('c-phone').value=c.phone||'';
  document.getElementById('c-address').value=c.address||'';
  document.getElementById('c-group').value=c.group_name||'';
  document.getElementById('c-disc').value=c.discount_pct||0;
  document.getElementById('c-limit').value=c.credit_limit||0;
  document.getElementById('c-opening').value=c.opening||0;
  document.getElementById('c-opening-date').value=c.opening_date||'';
  document.getElementById('c-notes').value=c.notes||'';
  partyModalKind = c.kind||partyKind;
  document.querySelectorAll('#party-modal .cust-only').forEach(el=>el.style.display=(partyModalKind==='customer')?'':'none');
  openModal('party-modal');
}
async function delCustomer(id){
  if(!confirm('حذف؟ (لا يمكن حذف من له فواتير أو دفعات)')) return;
  try{ await api('/api/customers/'+id,{method:'DELETE'}); toast('تم الحذف'); loadCustomers(); }
  catch(e){ toast('لا يمكن الحذف: له فواتير أو دفعات مسجلة', true); }
}

/* ---------- items master & pricing ---------- */
let itemsMaster=[];
let wholesaleDivisor=1.22;
async function loadItems(){
  try{ itemsMaster=await api('/api/items'); }catch(e){ itemsMaster=[]; }
  const tb=document.querySelector('#items-table tbody');
  if(tb) tb.innerHTML=itemsMaster.map(it=>`<tr>
    <td>${esc(it.name)}</td><td class="num">${esc(it.code||'')}</td><td class="num">${fmt(it.retail_price)}</td>
    <td class="num">${wholesaleDivisor?fmt(it.retail_price/wholesaleDivisor):'-'}</td>
    <td class="num">${it.cost?fmt(it.cost):'-'}</td>
    <td><button class="small ghost" onclick="editItem(${it.id})">تعديل</button></td></tr>`).join('');
}
function openItemModal(){
  document.getElementById('item-form-title').textContent='إضافة صنف';
  for(const id of ['it-id','it-name','it-code','it-retail','it-cost']) document.getElementById(id).value='';
  document.getElementById('it-div').textContent=wholesaleDivisor;
  openModal('item-modal');
}
function editItem(id){
  const it=itemsMaster.find(x=>x.id===id); if(!it) return;
  document.getElementById('item-form-title').textContent='تعديل صنف';
  document.getElementById('it-id').value=it.id;
  document.getElementById('it-name').value=it.name;
  document.getElementById('it-code').value=it.code||'';
  document.getElementById('it-retail').value=it.retail_price||'';
  document.getElementById('it-cost').value=it.cost||'';
  document.getElementById('it-div').textContent=wholesaleDivisor;
  openModal('item-modal');
}
async function saveItem(){
  if(!v('it-name')){toast('اكتب اسم الصنف',true);return;}
  try{
    await api('/api/items',{method:'POST',body:JSON.stringify({name:v('it-name'),code:v('it-code'),retail_price:v('it-retail')||0,cost:v('it-cost')||0})});
    toast('تم حفظ الصنف'); closeModal('item-modal'); loadItems(); loadItemNames();
  }catch(e){toast(e.message,true);}
}
function invIsCustomer(){ return KIND_PARTY[document.getElementById('i-kind').value]==='customer'; }
function custDiscPct(){ const c=customers.find(x=>x.id==v('i-customer')); return (c&&invIsCustomer())?(+c.discount_pct||0):0; }
function retailToWs(r){ return Math.round((r/(wholesaleDivisor||1.22))*(1-custDiscPct()/100)*100)/100; }
function seComputePrice(showToast){
  const name=v('se-item').trim();
  if(!name) return;
  const priceEl=document.getElementById('se-price');
  const hit=invoicePrices.find(p=>p.item_name===name);
  if(hit){ priceEl.value=hit.price; if(showToast) toast('سعر خاص للعميل (سعر مستهلك): '+fmt(hit.price)); seTotals(); return; }
  const it=itemsMaster.find(x=>x.name===name);
  if(it && it.retail_price){
    priceEl.value=it.retail_price;
    if(showToast) toast('سعر المستهلك المسجل: '+fmt(it.retail_price));
  } else {priceEl.value='';if(showToast)toast('الصنف ده ملهوش سعر مستهلك متسجل - سجله من تعريفات أول بالمبيعات', true); }
  seTotals();
}
function sePriceLookup(){ seComputePrice(false); }

/* ---------- invoices ---------- */
let itemNames=[];
async function loadItemNames(){
  try{ itemNames=await api('/api/item-names'); }catch(e){ itemNames=[]; }
  document.getElementById('se-items').innerHTML=itemNames.map(n=>`<option value="${esc(n)}">`).join('');
}
let colorsList=[];
const COLOR_DOTS={'أسود':'#111827','بني':'#7c4a1e','هافان':'#b5651d','أبيض':'#f8fafc','كحلي':'#1e3a5f','رمادي':'#9ca3af','تانجو':'#d97706','بيج':'#e7d3b3','عسلي':'#b45309','نبيتي':'#7f1d1d','زيتي':'#3f6212'};
async function loadColors(){
  try{ const s=await api('/api/settings'); colorsList=(s.colors||'').split(',').map(x=>x.trim()).filter(Boolean); wholesaleDivisor=parseFloat(s.wholesale_divisor)||1.22; }catch(e){}
  try{colorsList=[...new Set([...colorsList,...await api('/api/colors')])]}catch(e){}
  renderChips();
}
function renderChips(){if(typeof NixtColorPicker==='undefined')return;NixtColorPicker.quantityChips('se-chips',seTotals)}

function seTotals(){
  let pairs=0;
  document.querySelectorAll('#se-chips .se-chip').forEach(ch=>{ if(ch.querySelector('.se-chip-on').checked) pairs+=parseFloat(ch.querySelector('.se-chip-qty').value)||0; });
  const price=parseFloat(v('se-price'))||0;
  document.getElementById('se-total-pairs').textContent=fmt(pairs);
  document.getElementById('se-total-price').textContent=fmt(pairs*price);
  const wsEl=document.getElementById('se-total-ws');
  if(wsEl) wsEl.textContent=(invIsCustomer()&&price)?('الجملة بعد الخصم ≈ '+fmt(pairs*retailToWs(price))):'';
}
function seClear(){ document.getElementById('se-item').value=''; document.getElementById('se-price').value=''; renderChips(); seTotals(); }
function openColorsModal(){ renderColorsList(); document.getElementById('cl-new').value=''; openModal('colors-modal'); }
function renderColorsList(){
  document.getElementById('cl-list').innerHTML=colorsList.map((c,i)=>`<span class="se-chip"><span class="dot" style="background:${COLOR_DOTS[c]||'#a8a29e'}"></span>${esc(c)}<button class="small danger" onclick="colorsList.splice(${i},1);renderColorsList()">×</button></span>`).join('')||'<span class="muted">مفيش ألوان - أضف أول لون</span>';
}
function addColor(){ const n=v('cl-new').trim(); if(!n) return; if(!colorsList.includes(n)) colorsList.push(n); document.getElementById('cl-new').value=''; renderColorsList(); }
async function saveColors(){
  try{ await api('/api/settings',{method:'POST',body:JSON.stringify({colors:colorsList.join(',')})}); toast('اتحفظت الألوان'); closeModal('colors-modal'); renderChips(); }
  catch(e){toast(e.message,true);}
}
function seCommit(){
  const name=v('se-item').trim();
  if(!name){ toast('اكتب الصنف الأول', true); return; }
  const price=parseFloat(v('se-price'))||0;
  let added=0;
  document.querySelectorAll('#se-chips .se-chip').forEach(ch=>{
    if(!ch.querySelector('.se-chip-on').checked) return;
    const pairs=parseFloat(ch.querySelector('.se-chip-qty').value)||0;
    if(pairs>0){ addItemRow({item_name:name,color:ch.dataset.color,pairs:pairs,price:price}); added++; }
  });
  if(!added){ toast('علّم على لون واحد على الأقل واكتب العدد', true); return; }
  renderChips(); seTotals();
  calcInvoice();
}
async function showCloudBackups(){
 const box=document.getElementById('cloud-backup-list');box.classList.remove('hidden');box.textContent='جارٍ قراءة النسخ…';
 try{const r=await api('/api/cloud-backups');box.innerHTML='<h3>نسخ Drive الخاصة بقاعدة البيانات</h3><p>الاستعادة بتستبدل قاعدة البيانات الحالية بعد نسخة أمان. النسخ محفوظة في مجلد Nixt المخفي في حسابك، آخر 20 نسخة.</p>'+r.backups.map(x=>'<div>'+esc(x.name)+' <button class="small ghost" onclick="restoreCloudBackup(\''+esc(x.id)+'\')">استعادة</button></div>').join('')+'<button class="ghost" onclick="this.parentElement.classList.add(\'hidden\')">إغلاق</button>';}catch(e){box.textContent=e.message;}
}
async function restoreCloudBackup(id){if(!confirm('استبدال كل البيانات الحالية بنسخة Drive المختارة؟ هنعمل نسخة أمان محلية الأول.'))return;try{await api('/api/cloud-restore',{method:'POST',body:JSON.stringify({file_id:id})});toast('اتعملت الاستعادة');location.reload();}catch(e){toast(e.message,true);}}
async function refreshBackupStatus(){
 try{const b=await api('/api/backup-status');const e=document.getElementById('continuous-backup-status');e.textContent=b.last_error?'النسخ المحلي محتاج مراجعة: '+b.last_error:b.pending?'النسخة المحلية بتتحدث…':b.last_success?'آخر نسخة محلية: '+b.last_success:'النسخ المحلي التلقائي جاهز';e.title=b.path||'';const c=b.cloud||{};e.textContent+=' | نسخة قاعدة البيانات على Drive: '+(c.last_error|| (c.pending?'في الانتظار':c.last_success||'لسه مفيش نسخة'));}catch(e){}
}
setInterval(()=>{if(currentUser)refreshBackupStatus();},5000);
let stockBalances=[];
const stockTypeLabel={leather:'جلد',sole:'نعل',other:'خامة'};
const stockKindLabel={opening:'رصيد بداية',issue:'صرف',purchase:'شراء',purchase_return:'مرتجع شراء',reverse:'تصحيح عكسي'};
async function loadStock(){
 try{const r=await api('/api/stock');stockBalances=r.balances;
 document.getElementById('stock-balances').innerHTML=r.balances.map(x=>`<tr><td>${esc(x.item_name)}</td><td>${stockTypeLabel[x.material_type]||''}</td><td>${esc(x.color)}</td><td>${esc(x.size||(x.material_type==='sole'?'غير محدد':''))}</td><td class="num">${fmt(x.quantity)}</td><td>${esc(unitLabel(x.unit))}</td></tr>`).join('')||'<tr><td colspan="6">مفيش رصيد مسجل لسه</td></tr>';
 const soles=r.balances.filter(x=>x.material_type==='sole').map(x=>({...x,quantity:x.quantity}));document.getElementById('sole-stock-grid').innerHTML=soles.length?prodGrid(soles):'<p class=muted>مفيش رصيد نعل مسجل</p>';
 document.getElementById('stock-movements').innerHTML=r.movements.map(x=>`<tr><td class="num">${x.id}</td><td class="num">${esc(x.date)}</td><td>${esc(x.item_name)}</td><td>${esc(x.color)} ${esc(x.size||(x.material_type==='sole'?'غير محدد':''))}</td><td class="num">${fmt(x.quantity)} ${esc(unitLabel(x.unit))}</td><td>${stockKindLabel[x.kind]||esc(x.kind)}</td><td>${esc(x.reason)}${x.invoice_id?' | فاتورة #'+x.invoice_id:''}</td><td>${!x.invoice_id&&!x.reversed&&x.kind!=='reverse'?'<button class="small ghost" onclick="reverseStock('+x.id+')">تصحيح عكسي</button>':''}</td></tr>`).join('');
 }catch(e){toast(e.message,true);}
}
function stockUnit(){const t=v('st-type');const u=document.getElementById('st-unit');u.value=t==='leather'?'قدم²':t==='sole'?'زوج':'';u.readOnly=t!=='other';}
function stockOpen(kind){
 document.getElementById('st-kind').value=kind;document.getElementById('stock-form-title').textContent=kind==='opening'?'رصيد بداية':'صرف من المخزن';
 for(const id of ['st-name','st-color','st-size','st-qty','st-reason'])document.getElementById(id).value='';
 document.getElementById('st-date').value=today();stockUnit();
 document.getElementById('st-existing').innerHTML='<option value="">-- اختر الصنف --</option>'+stockBalances.map((x,i)=>'<option value="'+i+'">'+esc(x.item_name+' / '+x.color+' / '+x.size+' / '+unitLabel(x.unit)+' / رصيد '+fmt(x.quantity))+'</option>').join('');
 document.getElementById('stock-form').classList.remove('hidden');
}
function stockChoose(){const n=v('st-existing');if(n==='')return;const x=stockBalances[+n];for(const [id,k] of [['st-name','item_name'],['st-color','color'],['st-size','size'],['st-type','material_type'],['st-unit','unit']])document.getElementById(id).value=k==='unit'?unitLabel(x[k]):x[k];if(document.getElementById('st-size-mode')){document.getElementById('st-size-mode').value=x.size?'specified':'unspecified';document.getElementById('st-size').disabled=!x.size;}}
async function saveStockMove(){
 const b={kind:v('st-kind'),item_name:v('st-name').trim(),material_type:v('st-type'),color:v('st-color').trim(),size:v('st-size').trim(),unit:v('st-type')==='leather'?'sqft':v('st-type')==='sole'?'pair':v('st-unit').trim(),pairs:+v('st-qty'),date:v('st-date'),reason:v('st-reason').trim()};
 if(!b.reason||!b.item_name||!(b.pairs>0)){toast('راجع الصنف والكمية والسبب',true);return;}
 if(!confirm('تسجيل '+(stockKindLabel[b.kind]||'حركة')+' للصنف '+b.item_name+'؟'))return;
 try{await api('/api/stock/move',{method:'POST',body:JSON.stringify(b)});toast('اتسجلت الحركة');document.getElementById('stock-form').classList.add('hidden');loadStock();}catch(e){toast(e.message,true);}
}
async function reverseStock(id){const reason=prompt('سبب تصحيح الحركة؟');if(!reason||!reason.trim())return;try{await api('/api/stock/move',{method:'POST',body:JSON.stringify({kind:'reverse',movement_id:id,reason:reason.trim(),date:today()})});toast('اتسجل التصحيح العكسي');loadStock();}catch(e){toast(e.message,true);}}
let noticeItems=[],noticeUpdate=null,noticeUpdateCheck=0,noticeOwner=null;
function noticeKey(){return 'nixt-read-'+(currentUser?.username||'user');}
function noticeRead(){try{const r=JSON.parse(localStorage.getItem(noticeKey())||'[]');return Array.isArray(r)?r:[];}catch(e){return [];}}
function renderNotices(){const read=noticeRead();const list=noticeUpdate?[noticeUpdate,...noticeItems]:noticeItems;document.getElementById('notice-count').textContent=list.filter(x=>!read.includes(x.id)).length;document.getElementById('notice-list').innerHTML=list.length?list.map(x=>'<button class="ghost" style="display:block;width:100%;text-align:right;margin:8px 0;border-right:4px solid '+(read.includes(x.id)?'var(--line)':'var(--accent)')+'" onclick="openNotice('+list.indexOf(x)+')"><b>'+esc(x.title)+'</b><div class="muted" style="font-size:12px;white-space:normal">'+esc(x.detail)+'</div></button>').join(''):'<p class="muted">مفيش إشعارات حالياً</p>';}
function toggleNotices(){document.getElementById('notice-panel').classList.toggle('hidden');refreshNotices();}
function readAllNotices(){localStorage.setItem(noticeKey(),JSON.stringify((noticeUpdate?[noticeUpdate,...noticeItems]:noticeItems).map(x=>x.id)));renderNotices();}
function openNotice(n){const x=(noticeUpdate?[noticeUpdate,...noticeItems]:noticeItems)[n];const ids=noticeRead();ids.push(x.id);localStorage.setItem(noticeKey(),JSON.stringify(ids.slice(-200)));document.getElementById('notice-panel').classList.add('hidden');if(x.tab==='update')openUpdate();else showTab(x.tab);renderNotices();}
async function refreshNotices(){
 if(!currentUser||lockedNow)return;
 const owner=currentUser.username,role=currentUser.role;
 if(noticeOwner!==owner){noticeOwner=owner;noticeItems=[];noticeUpdate=null;noticeUpdateCheck=0;document.getElementById('notice-panel').classList.add('hidden');}
 if(role!=='admin')noticeUpdate=null;
 try{
  const result=await api('/api/notifications');
  if(!currentUser||currentUser.username!==owner||currentUser.role!==role||lockedNow)return;
  noticeItems=result.alerts||[];renderNotices();
  if(role==='admin'&&Date.now()-noticeUpdateCheck>3600000){
   noticeUpdateCheck=Date.now();const u=await api('/api/check-update');
   if(!currentUser||currentUser.username!==owner||currentUser.role!==role||lockedNow)return;
   noticeUpdate=u.update_available?{id:'update-'+u.latest,title:'تحديث جديد '+u.latest,detail:'اضغط لعرض تفاصيل التحديث',tab:'update'}:null;
   renderNotices();
  }
 }catch(e){}
}
setInterval(refreshNotices,30000);
function pmPacksToggle(){
 const active=v('pm-type')==='leather'&&document.getElementById('pm-use-packs').checked;
 document.getElementById('pm-packs-editor').classList.toggle('hidden',!active);
 document.getElementById('pm-quantity').readOnly=active;
 if(active&&!document.querySelector('.pm-pack'))pmAddPack();
 if(active)pmPackSum();
}
function pmAddPack(){
 if(document.querySelectorAll('.pm-pack').length>=200){toast('الحد 200 باكو في السطر، قسمها على سطور أكتر',true);return;}
 const p=document.createElement('div');p.className='pm-pack';p.style='border:1px solid #ecd9be;border-radius:8px;padding:10px;margin:8px 0';
 p.innerHTML='<div class="row"><input class="pack-label" placeholder="رقم / وصف الباكو (اختياري)"><label>عدد الجلدات <input class="pack-count num" type="number" min="1" max="500" value="1" style="width:70px" onchange="pmPackCount(this)"></label><b>المجموع: <span class="pack-total num">0</span> قدم²</b><button class="small danger" onclick="this.closest(\'.pm-pack\').remove();pmPackSum()">حذف الباكو</button></div><div class="pack-hides row" style="margin-top:8px"></div>';
 document.getElementById('pm-packs').appendChild(p);pmPackCount(p.querySelector('.pack-count'));
}
function pmPackCount(input){
 const pack=input.closest('.pm-pack'),box=pack.querySelector('.pack-hides');const n=+input.value;
 if(!Number.isInteger(n)||n<1||n>500){toast('عدد الجلدات من 1 إلى 500',true);return;}
 const old=[...box.querySelectorAll('input')].map(x=>x.value);
 if(n<old.length&&old.slice(n).some(x=>+x>0)&&!confirm('تقليل العدد هيمسح مساحات الجلدات الزيادة. متأكد؟')){input.value=old.length;return;}
 box.innerHTML=Array.from({length:n},(_,i)=>'<label style="font-size:12px">جلدة '+(i+1)+'<input class="hide-sqft num" type="number" min="0" step="0.01" style="width:90px;display:block" placeholder="قدم²" value="'+esc(old[i]||'')+'" oninput="pmPackSum()" onkeydown="pmHideKey(event,this)"></label>').join('');pmPackSum();
}
function pmHideKey(event,input){if(event.key!=='Enter')return;event.preventDefault();const p=input.closest('.pm-pack'),fields=[...p.querySelectorAll('.hide-sqft')],n=fields.indexOf(input);if(n+1<fields.length){fields[n+1].focus();fields[n+1].select();}else{const count=p.querySelector('.pack-count');count.value=fields.length+1;pmPackCount(count);const next=p.querySelectorAll('.hide-sqft')[n+1];if(next)next.focus();}}
function pmReadPacks(){return [...document.querySelectorAll('.pm-pack')].map(p=>({label:p.querySelector('.pack-label').value.trim(),hides:[...p.querySelectorAll('.hide-sqft')].map(x=>+x.value)}));}
function pmPackSum(){let total=0;document.querySelectorAll('.pm-pack').forEach(p=>{const q=[...p.querySelectorAll('.hide-sqft')].reduce((a,x)=>a+(+x.value||0),0);p.querySelector('.pack-total').textContent=fmt(q);total+=q;});document.getElementById('pm-packs-total').textContent=fmt(total);if(document.getElementById('pm-use-packs').checked)document.getElementById('pm-quantity').value=+total.toFixed(6);}
function parsePacks(x){try{return typeof x==='string'?JSON.parse(x):x||[];}catch(e){return [];}}
function packDetails(packs){return packs.map((p,n)=>'<div style="margin:4px 0;padding:4px 7px;background:#fff8e7;border-radius:4px;break-inside:avoid">باكو '+esc(p.label||String(n+1))+' | '+p.hides.length+' جلدة | <b class="num">'+fmt(p.hides.reduce((a,x)=>a+(+x),0))+' قدم²</b> <span class="num" dir="ltr">('+p.hides.map(x=>fmt(x)).join(' + ')+')</span></div>').join('');}
function pmRender(){
  const sole=v('pm-type')==='sole', other=v('pm-type')==='other';
  document.getElementById('pm-unit-field').classList.toggle('hidden',!other);
  document.getElementById('pm-quantity-label').textContent=other?'الكمية':'المساحة (قدم مربع)';
  document.getElementById('pm-leather').classList.toggle('hidden',sole);
  document.getElementById('pm-sole').classList.toggle('hidden',!sole);
  document.getElementById('pm-price-label').textContent=sole?'سعر الزوج':other?'سعر الوحدة':'سعر القدم المربع';
  document.getElementById('pm-packs-panel').classList.toggle('hidden',sole||other);pmPacksToggle();
  if(sole&&!document.querySelector('#pm-grid input')) pmGrid();if(sole)pmColorTiles();
}
function pmGrid(){
  const split=id=>[...new Set(v(id).split(/[,،]/).map(x=>x.trim()).filter(Boolean))];
  const grid=document.getElementById('pm-grid'),oldMode=grid.dataset.mode||'sizes',mode=v('pm-sole-mode')||'sizes';window.pmSizeDrafts=window.pmSizeDrafts||{sizes:{},totals:{}};document.querySelectorAll('#pm-grid .pm-cell').forEach(x=>pmSizeDrafts[oldMode][JSON.stringify([x.dataset.color,x.dataset.size])]=x.value);grid.dataset.mode=mode;const sizes=mode==='totals'?['']:[...new Set(['40','41','42','43','44','45','46','47','48',...split('pm-sizes'),...stockBalances.filter(x=>x.material_type==='sole'&&(!v('pm-name').trim()||x.item_name===v('pm-name').trim())).map(x=>x.size).filter(Boolean)])],colors=split('pm-colors');
  if(!sizes.length||!colors.length){toast('اكتب المقاسات والألوان',true);return;}
  const keep=pmSizeDrafts[mode];document.getElementById('pm-mode-note').textContent=mode==='totals'?'يتحفظ في المخزن تحت مقاس غير محدد، من غير توزيع تلقائي.':'';document.getElementById('pm-size-options').classList.add('hidden');
  document.getElementById('pm-grid').innerHTML='<table><thead><tr><th>اللون / المقاس</th>'+sizes.map(x=>'<th>'+esc(x||'غير محدد')+'</th>').join('')+'<th>إجمالي اللون</th></tr></thead><tbody>'+colors.map(c=>'<tr><th>'+esc(c)+'</th>'+sizes.map(z=>'<td><input type="number" min="0" step="1" class="num pm-cell" style="width:60px" data-color="'+esc(c)+'" data-size="'+esc(z)+'" value="'+esc(keep[JSON.stringify([c,z])]||'')+'" placeholder="0" oninput="pmGridTotals()"></td>').join('')+'<td class="pm-row-total num">0</td></tr>').join('')+'</tbody><tfoot><tr><th>الإجمالي</th>'+sizes.map(z=>'<th class="pm-col-total" data-size="'+esc(z)+'">0</th>').join('')+'<th id="pm-grand-total">0</th></tr></tfoot></table>';pmGridTotals();pmColorTiles();
}
function pmCommit(){
  const name=v('pm-name').trim(),type=v('pm-type'),price=+v('pm-price');
  if(!name||!Number.isFinite(price)||price<0){toast('راجع اسم الخامة والسعر',true);return;}
  const added=[];
  if(type!=='sole'){
    const q=+v('pm-quantity');if(!(q>0)||(type==='other'&&!v('pm-unit').trim())){toast('راجع الكمية والوحدة',true);return;}
    const packs=type==='leather'&&document.getElementById('pm-use-packs').checked?pmReadPacks():[];
    if(packs.reduce((a,p)=>a+p.hides.length,0)>2000){toast('الحد 2000 جلدة في السطر، قسمها على سطور أكتر',true);return;}
    if(packs.some(p=>!p.hides.length||p.hides.some(x=>!Number.isFinite(x)||x<=0))){toast('اكتب مساحة موجبة لكل جلدة',true);return;}
    added.push({leather_packs:packs,item_name:name,color:v('pm-color').trim(),pairs:q,price,material_type:type,unit:type==='leather'?'sqft':v('pm-unit').trim(),size:''});
  }else{
    for(const cell of document.querySelectorAll('#pm-grid .pm-cell')){
      const q=+cell.value;if(q<0||!Number.isInteger(q)){toast('عدد الأزواج لازم يكون عدد صحيح موجب',true);return;}
      if(q>0)added.push({item_name:name,color:cell.dataset.color,size:cell.dataset.size,pairs:q,price,material_type:type,unit:'pair'});
    }
    if(!added.length){toast('اكتب أزواج في الشبكة الأول',true);return;}
  }
  added.forEach(addItemRow);calcInvoice();
  document.getElementById('pm-packs').innerHTML='';document.getElementById('pm-packs-total').textContent='0';
  document.getElementById('pm-quantity').value='';window.pmSizeDrafts={sizes:{},totals:{}};document.querySelectorAll('#pm-grid input').forEach(x=>x.value='');pmGridTotals();
}
function isSupInvoice(i){return i.kind==='purchase'||i.kind==='purchase_return';}
function unitLabel(unit){return unit==='sqft'?'قدم²':unit==='pair'?'زوج':unit||'زوج';}
function quantitySummary(items){
  const sums={};items.forEach(x=>{const u=x.unit||'pair';sums[u]=(sums[u]||0)+(+x.pairs||0);});
  return Object.entries(sums).map(([u,q])=>fmt(q)+' '+unitLabel(u)).join(' + ')||'0';
}
function renderItemsHead(){
  document.getElementById('i-items-head').innerHTML = invIsCustomer()
    ? '<th style="width:30%">الصنف</th><th>اللون</th><th>أزواج</th><th>سعر المستهلك</th><th>جملة/زوج</th><th>الإجمالي</th><th></th>'
    : '<th>الخامة</th><th>اللون</th><th>الكمية</th><th>سعر الوحدة</th><th>الإجمالي</th><th></th>';
}
function addItemRow(it){
  it = it||{};
  const tr = document.createElement('tr');
  tr.dataset.leatherPacks=JSON.stringify(parsePacks(it.leather_packs));
  tr.dataset.materialType=it.material_type||'';tr.dataset.unit=it.unit||'pair';tr.dataset.size=it.size||'';
  if(invIsCustomer()){
    tr.innerHTML = `<td><input class="it-name" value="${esc(it.item_name||'')}" onchange="priceLookup(this)"></td>
      <td><input class="it-color" value="${esc(it.color||'')}"></td>
      <td><input class="it-pairs num" type="number" min="0" step="1" value="${it.pairs??''}" oninput="calcInvoice()"></td>
      <td><input class="it-retail num" type="number" min="0" step="0.01" value="${it.retail_price??it.price??''}" oninput="calcInvoice()"></td>
      <td class="it-ws num muted">0</td>
      <td class="it-total num">0</td>
      <td><button class="small danger" onclick="this.closest('tr').remove();calcInvoice()">×</button></td>`;
  }else{
    tr.innerHTML = `<td><input class="it-name" value="${esc(it.item_name||'')}" onchange="priceLookup(this)"><small>${it.material_type==='leather'?'جلد':it.material_type==='sole'?'نعل':it.material_type==='other'?'خامة':''}${it.size?' | مقاس '+esc(it.size):it.material_type==='sole'?' | مقاس غير محدد':''}</small></td>
      <td><input class="it-color" value="${esc(it.color||'')}"></td>
      <td><input class="it-pairs num" type="number" min="0" step="${it.material_type==='sole'?'1':'0.01'}" value="${it.pairs??''}" oninput="calcInvoice()"><small>${it.unit==='sqft'?'قدم²':it.unit==='pair'?'زوج':esc(it.unit||'زوج')}</small></td>
      <td><input class="it-price num" type="number" min="0" step="0.01" value="${it.price??''}" oninput="calcInvoice()"></td>
      <td class="it-total num">0</td>
      <td><button class="small danger" onclick="this.closest('tr').remove();calcInvoice()">×</button></td>`;
  }
  const packs=parsePacks(it.leather_packs);if(packs.length){tr.querySelector('.it-pairs').readOnly=true;tr.querySelector('td').insertAdjacentHTML('beforeend',packDetails(packs)+'<button class="small ghost" onclick="pmEditPacks(this)">تعديل البواكي</button>');}
  document.getElementById('i-items').appendChild(tr);
}
function pmEditPacks(button){
 const tr=button.closest('tr');if(document.querySelectorAll('.pm-pack').length&&!confirm('استبدال البواكي اللي لسه بتدخلها بتفاصيل السطر ده؟'))return;
 document.getElementById('pm-type').value='leather';pmRender();
 document.getElementById('pm-name').value=tr.querySelector('.it-name').value;document.getElementById('pm-color').value=tr.querySelector('.it-color').value;document.getElementById('pm-price').value=tr.querySelector('.it-price').value;
 document.getElementById('pm-use-packs').checked=true;document.getElementById('pm-packs').innerHTML='';
 parsePacks(tr.dataset.leatherPacks).forEach(p=>{pmAddPack();const box=document.querySelector('.pm-pack:last-child');box.querySelector('.pack-label').value=p.label||'';box.querySelector('.pack-count').value=p.hides.length;pmPackCount(box.querySelector('.pack-count'));box.querySelectorAll('.hide-sqft').forEach((x,i)=>x.value=p.hides[i]);});
 pmPacksToggle();pmPackSum();tr.remove();calcInvoice();document.getElementById('pm-packs-panel').scrollIntoView({block:'center'});
}
function readItems(){
  const items=[]; let total=0, pairs=0, retailTotal=0;
  document.querySelectorAll('#i-items tr').forEach(tr=>{
    const name=tr.querySelector('.it-name').value.trim();
    const p=parseFloat(tr.querySelector('.it-pairs').value)||0;
    const rEl=tr.querySelector('.it-retail');
    let pr, retail=null;
    if(rEl){
      retail=parseFloat(rEl.value)||0;
      pr=retailToWs(retail);
      const wsCell=tr.querySelector('.it-ws'); if(wsCell) wsCell.textContent=retail?fmt(pr):'';
    }else{
      pr=parseFloat(tr.querySelector('.it-price').value)||0;
    }
    const t=p*pr; tr.querySelector('.it-total').textContent=fmt(t);
    if(name){
      const o={item_name:name,color:tr.querySelector('.it-color').value.trim(),pairs:p,material_type:tr.dataset.materialType||'',unit:tr.dataset.unit||'pair',size:tr.dataset.size||''};
      o.leather_packs=parsePacks(tr.dataset.leatherPacks);
      if(rEl){ o.retail_price=retail; } else { o.price=pr; }
      items.push(o); total+=t; pairs+=p; if(retail!=null) retailTotal+=p*retail;
    }
  });
  return {items,total,pairs,retailTotal};
}
function calcInvoice(){
  const {total,pairs,retailTotal}=readItems();
  const ship=parseFloat(v('i-shipping'))||0, other=parseFloat(v('i-other'))||0, disc=parseFloat(v('i-discount'))||0;
  document.getElementById('i-items-total').textContent=fmt(total);
  const rt=document.getElementById('i-retail-total');
  if(rt) rt.textContent=invIsCustomer()?('مستهلك: '+fmt(retailTotal)):'';
  document.getElementById('i-pairs').textContent=invIsCustomer()?fmt(pairs):quantitySummary(readItems().items);
  document.getElementById('i-net').textContent=fmt(total+ship+other-disc);
}
async function onInvoiceCustomer(){
  const c = customers.find(x=>x.id==v('i-customer'));
  const info=document.getElementById('i-cust-info');
  if(c){
    const isCust=KIND_PARTY[document.getElementById('i-kind').value]==='customer';
    const bal=c.balance||0;
    info.innerHTML=(c.code?`<span class="badge">كود: <span class="num">${esc(c.code)}</span></span>`:'')
      +(isCust&&c.discount_pct?`<span class="badge" style="background:var(--accent-soft);border-color:#ecd9be">خصم <span class="num">${c.discount_pct}%</span></span>`:'')
      +`<span class="badge">الرصيد السابق: <span class="num" style="color:${bal>0?'var(--bad)':'var(--ok)'}">${fmt(bal)}</span> ج</span>`;
    info.classList.remove('hidden');
  } else { info.classList.add('hidden'); }
  invoicePrices=[];
  if(c && KIND_PARTY[document.getElementById('i-kind').value]==='customer'){
    try{ invoicePrices=await api('/api/prices?customer_id='+c.id); }catch(e){}
  }
  calcInvoice();
}
async function saveInvoice(andPrint){
  const cid=v('i-customer'); if(!cid){toast('اختر الطرف أولاً',true);return;}
  const {items}=readItems(); if(!items.length){toast('أضف صنفاً واحداً على الأقل',true);return;}
  const kind=document.getElementById('i-kind').value;
  if(kind==='sale'){
    const c=customers.find(x=>x.id==+cid);
    if(c && (c.credit_limit||0)>0){
      const net=parseFloat((document.getElementById('i-net').textContent||'0').replace(/,/g,''))||0;
      const after=(c.balance||0)+net;
      if(after>c.credit_limit+0.005){
        if(!confirm('تحذير حد الائتمان:\n'+c.name+' هيتجاوز حد الائتمان ('+fmt(c.credit_limit)+' ج) بعد الفاتورة دي - هيبقى عليه '+fmt(after)+' ج.\nتحفظ الفاتورة برضه؟')) return;
      }
    }
  }
  const warns=[];
  const invDate=v('i-date')||today();
  const ageDays=Math.round((Date.parse(today())-Date.parse(invDate))/864e5);
  if(ageDays>30) warns.push('التاريخ قديم: '+invDate+' (من '+ageDays+' يوم)');
  if(kind==='sale'||kind==='purchase'){
    const zeroItems=items.filter(x=>!((+x.price)||(+x.retail_price)));
    if(zeroItems.length) warns.push('سعر صفر في: '+zeroItems.map(x=>x.item_name).slice(0,3).join('، ')+(zeroItems.length>3?' وغيرهم':''));
    const netNow=parseFloat((document.getElementById('i-net').textContent||'0').replace(/,/g,''))||0;
    try{
      const all=await api('/api/invoices?kind='+kind);
      const dup=all.find(x=>x.customer_id==+cid && x.date===invDate && Math.abs((x.net||0)-netNow)<0.01);
      if(dup) warns.push('في '+(KIND_LABEL[kind]||kind)+' لنفس الطرف بنفس التاريخ ونفس الصافي ('+(dup.number||('#'+dup.id))+') - متأكد إنها مش مكررة؟');
    }catch(e){}
  }
  if(warns.length && !confirm('تنبيه قبل الحفظ:\n- '+warns.join('\n- ')+'\n\nتحفظ برضه؟')) return;
  const body={customer_id:+cid, date:invDate, kind,
    shipping:v('i-shipping')||0, other_charges:v('i-other')||0, discount:v('i-discount')||0,
    notes:v('i-notes'), reason:v('i-reason'), return_of:v('i-return-of')||null, items,production_selection:typeof prodSelection==='undefined'?[]:prodSelection};
  const doSave=async(b)=>{
    const r=await api('/api/invoices',{method:'POST',body:JSON.stringify(b)});
    toast('تم حفظ '+KIND_LABEL[kind]+' رقم '+r.id);
    const wasReturn=(kind==='return'||kind==='purchase_return');
    resetInvoiceForm(); loadInvoices(); refreshCustomers(); loadItemNames();
    if(andPrint) openPrintChoice(r.id);
    if(wasReturn) showTab(kind==='return'?'sales':'purchases');
  };
  try{
    await doSave(body);
  }catch(e){
    if(e.need_approval){
      const t=await askManagerPin('الخصم أكبر من المسموح'+(e.limit?' ('+fmt(e.limit)+' ج)':'')+' - محتاج موافقة المدير بالرقم السري.');
      if(!t) return;
      body.approval=t;
      try{ await doSave(body); }catch(e2){ toast(e2.message,true); }
      return;
    }
    toast(e.message,true);
  }
}
async function lastInvoice(){
  try{
    const invs=await api('/api/invoices');
    const cid=v('i-customer');
    const list=cid?invs.filter(i=>String(i.customer_id)===String(cid)):invs;
    if(!list.length){toast('مفيش فواتير لسه',true);return;}
    openPrintChoice(list[0].id);
  }catch(e){toast(e.message,true);}
}
function resetInvoiceForm(){
  if(typeof prodSelection!=='undefined')prodSelection=[];
  if(typeof prodInvoiceLock==='function')prodInvoiceLock(false);
  window.pmSizeDrafts={sizes:{},totals:{}};document.querySelectorAll('#pm-grid input').forEach(x=>x.value='');pmGridTotals();
  document.getElementById('i-items').innerHTML=''; addItemRow();
  document.getElementById('se-item').value=''; document.getElementById('se-price').value='';
  document.getElementById('se-chips').innerHTML='';renderChips(); seTotals();
  for(const id of ['i-shipping','i-other','i-discount']) document.getElementById(id).value=0;
  document.getElementById('i-notes').value=''; document.getElementById('i-reason').value='';
  document.getElementById('i-return-of').value='';
  document.getElementById('i-return-banner').classList.add('hidden');
  calcInvoice();
}
async function loadInvoices(){
  await refreshCustomers();
  const invs=await api('/api/invoices');
  document.querySelector('#inv-table tbody').innerHTML=invs.slice(0,80).map(i=>`<tr>
    <td class="num">${esc(i.number||('#'+i.id))}</td><td class="num">${i.date}</td><td>${esc(i.customer_name)}</td>
    <td>${(i.kind==='return'||i.kind==='purchase_return')?'<span class="bad">'+KIND_LABEL[i.kind]+'</span>':KIND_LABEL[i.kind]||i.kind}</td>
    <td class="num">${isSupInvoice(i)?quantitySummary(i.items||[]):fmt(i.pairs)}</td><td class="num">${fmt(i.net)}</td>
    <td style="white-space:nowrap"><button class="small" onclick="openPrintChoice(${i.id})">طباعة</button>
    ${(i.kind==='sale'||i.kind==='return')?`<button class="small ghost" onclick="repriceInvoice(${i.id})" title="لو السعر اللي اتكتب في الفاتورة دي كان سعر مستهلك - اضغط هنا للتصحيح">تصحيح سعر</button>`:''}
    <button class="small ghost" onclick="dupInvoice(${i.id})" title="نسخ الفاتورة دي لفاتورة جديدة">تكرار</button>
    <button class="small danger" ${canDeleteDocs()?'':'style="display:none"'} onclick="delInvoice(${i.id})">حذف</button></td></tr>`).join('');
  filterTable('inv-table', document.getElementById('inv-search').value);
}
function canDeleteDocs(){
  if(!currentUser) return false;
  if(currentUser.role==='admin') return true;
  if(currentUser.perms && currentUser.perms.ops && currentUser.perms.ops.indexOf('delete_docs')>=0) return false;
  return true;
}
async function repriceInvoice(id){
  if(!confirm('تصحيح تفسير أسعار الفاتورة دي:\nالسعر المكتوب فيها هيتعتبر "سعر مستهلك"، والجملة هيتحسب منه (مستهلك ÷ القاسم، وبعدين خصم العميل).\nالأرصدة والتقارير هتتحدث لوحدها. متأكد؟')) return;
  try{
    const r=await api('/api/invoices/'+id+'/reprice-retail',{method:'POST',body:'{}'});
    toast('اتصححت - الصافي بقى '+fmt(r.new_net)+' ج بدل '+fmt(r.old_net)+' ج');
    loadInvoices();
  }catch(e){toast(e.message,true);}
}
async function delInvoice(id, tok, confirmed){
  if(!confirmed && !confirm('حذف الفاتورة رقم '+id+'؟')) return;
  try{
    await api('/api/invoices/'+id,{method:'DELETE',headers:tok?{'X-Approval':tok}:{}});
    toast('تم الحذف'); loadInvoices();
  }catch(e){
    if(e.need_approval){ const t=await askManagerPin('حذف فاتورة محتاج موافقة المدير.'); if(t) delInvoice(id,t,true); return; }
    toast(e.message,true);
  }
}
async function dupInvoice(id){
  prodSelection=[];
  try{
    const inv=await api('/api/invoices/'+id);
    document.getElementById('i-kind').value=inv.kind; onInvoiceKind();
    document.getElementById('i-customer').value=inv.customer_id; onInvoiceCustomer();
    document.getElementById('i-date').value=today();
    for(const x of ['i-shipping','i-other','i-discount']) document.getElementById(x).value=0;
    document.getElementById('i-notes').value=''; document.getElementById('i-reason').value=''; document.getElementById('i-return-of').value='';
    document.getElementById('i-items').innerHTML='';
    inv.items.forEach(it=>addItemRow({...it}));
    calcInvoice(); showTab('invoice');
    toast('اتعملت نسخة من فاتورة '+(inv.number||('#'+id))+' - راجعها واحفظها');
  }catch(e){toast(e.message,true);}
}
function companyHeader(co, title){
  return `<div class="dochead">
    <img src="/static/logo.png" alt="NIXT">
    <div><b>${esc(co.company_name||'')}</b><div class="co">
    ${co.company_phone?'تليفون: <span class="num">'+esc(co.company_phone)+'</span>':''}
    ${co.company_address?' — '+esc(co.company_address):''}</div></div>
    <div class="doctitle" style="margin-right:auto;text-align:left"><b>${title}</b></div>
  </div>`;
}
let printChoiceId = null;
function openPrintChoice(id){
  printChoiceId = id;
  openModal('print-choice');
}
async function printInvoice(id, mode){
  closeModal('print-choice');
  mode = mode || 'retail';
  const i = await api('/api/invoices/'+id);
  const isRet = i.kind==='return'||i.kind==='purchase_return';
  const isSup = i.customer && i.customer.kind==='supplier';
  const dual = mode==='dual' && !isSup;
  const partyLabel = isSup ? 'المورد' : 'العميل';
  const baseTitle = KIND_LABEL[i.kind]||'فاتورة';
  const title = dual ? baseTitle+' — أسعار جملة ومستهلك' : baseTitle;
  const subtitle = dual ? 'سعر المستهلك & جملة الجوز بعد الخصم' : 'إدارة المبيعات والتوزيع — INVOICE';
  const chip = c => `<span class="inv-chip"><span class="cdot" style="background:${COLOR_DOTS[c]||'#a8a29e'}"></span>${esc(c)}</span>`;
  const printItems=[];const soleGroups=new Map();
  i.items.forEach(x=>{if(isSup&&x.material_type==='sole'){const key=JSON.stringify([x.item_name,x.color,x.price,x.unit]);let g=soleGroups.get(key);if(!g){g={...x,pairs:0,sizes:[]};soleGroups.set(key,g);printItems.push(g);}g.pairs+=+x.pairs;g.sizes.push({size:x.size,quantity:x.pairs});}else printItems.push(x);});
  const rows = printItems.map((x,n)=>{
    if(dual){
      const retail = x.retail_price;
      return `<tr><td class="num">${n+1}</td><td>${esc(x.item_name)}${x.sizes?'<div>'+x.sizes.map(z=>'<span class="inv-chip">مقاس '+esc(z.size||'غير محدد')+': <b class="num">'+fmt(z.quantity)+'</b></span>').join(' ')+'</div>':x.size?' | مقاس '+esc(x.size):''}${packDetails(parsePacks(x.leather_packs))}</td><td>${chip(x.color)}</td>
        <td class="num">${fmt(x.pairs)} زوج</td>
        <td class="num">${retail!=null?fmt(retail)+' ج.م':'—'}</td>
        <td class="num ws">${fmt(x.price)} ج.م</td>
        <td class="num"><b>${fmt(x.pairs*x.price)} ج.م</b></td></tr>`;
    }
    return `<tr><td class="num">${n+1}</td><td>${esc(x.item_name)}${x.sizes?'<div>'+x.sizes.map(z=>'<span class="inv-chip">مقاس '+esc(z.size||'غير محدد')+': <b class="num">'+fmt(z.quantity)+'</b></span>').join(' ')+'</div>':x.size?' | مقاس '+esc(x.size):''}${packDetails(parsePacks(x.leather_packs))}</td><td>${chip(x.color)}</td>
      <td class="num">${fmt(x.pairs)} ${esc(unitLabel(x.unit))}</td><td class="num">${fmt(x.price)}</td>
      <td class="num">${fmt(x.pairs*x.price)} ج.م</td></tr>`;
  }).join('');
  const totalPairs = i.items.reduce((a,x)=>a+(+x.pairs||0),0);
  const retailTotal = i.items.reduce((a,x)=>a+(x.retail_price!=null?x.pairs*x.retail_price:0),0);
  const discTotal = Math.max(retailTotal - i.items_total, 0);
  const bal = i.party_balance || 0;
  const cards = dual
    ? `<div class="inv-card"><div class="l">${partyLabel} المكرم</div><div class="b">${esc(i.customer?i.customer.name:'')}</div>${i.customer&&i.customer.code?'<div class="s">كود: <span class="num">'+esc(i.customer.code)+'</span></div>':''}</div>
       <div class="inv-card"><div class="l">الإجمالي بسعر المستهلك</div><div class="b num">${fmt(retailTotal)} ج.م</div></div>
       <div class="inv-card"><div class="l">إجمالي الخصم التجاري</div><div class="b num bad">${fmt(discTotal)} ج.م</div></div>
       <div class="inv-card hl"><div class="l">الإجمالي بعد الخصم (جملة)</div><div class="b num">${fmt(i.net)} ج.م</div></div>`
    : `<div class="inv-card"><div class="l">${partyLabel} المكرم</div><div class="b">${esc(i.customer?i.customer.name:'')}</div>${i.customer&&i.customer.code?'<div class="s">كود: <span class="num">'+esc(i.customer.code)+'</span></div>':''}</div>
       <div class="inv-card"><div class="l">تاريخ الفاتورة</div><div class="b num">${i.date}</div></div>
       <div class="inv-card hl"><div class="l">إجمالي الفاتورة الصافي</div><div class="b num">${fmt(i.net)} ج.م</div></div>
       <div class="inv-card ${bal>0?'due':''}"><div class="l">${isSup?'الرصيد المستحق للمورد':'الرصيد المستحق على العميل'}</div><div class="b num">${fmt(Math.abs(bal))} ج.م</div><div class="s">${bal>0?'مدين':'لا يوجد رصيد'}</div></div>`;
  const thead = dual
    ? '<tr><th>#</th><th>الموديل / الصنف</th><th>لون الصنف</th><th>الأزواج</th><th>سعر المستهلك</th><th>جملة الجوز بعد الخصم</th><th>الإجمالي بعد الخصم</th></tr>'
    : '<tr><th>#</th><th>الموديل / الصنف</th><th>لون الصنف</th><th>الكمية / الوحدة</th><th>سعر الوحدة</th><th>الإجمالي الصافي</th></tr>';
  const totalBand = dual
    ? `<div class="inv-totalband"><div>إجمالي الأزواج ومبالغ الفاتورة:</div>
       <div><b class="num">${fmt(totalPairs)} زوج</b> &nbsp;|&nbsp; <span class="num" style="color:#8a7a66">${fmt(retailTotal)}</span> &nbsp;|&nbsp; <b class="num" style="color:#b45309">${fmt(i.net)} ج.م</b></div></div>`
    : `<div class="inv-totalband"><div>إجمالي كميات الفاتورة: <b class="num">${isSup?quantitySummary(i.items):fmt(totalPairs)+' زوج'}</b></div>
       <div>إجمالي قيمة الفاتورة الصافية: <b class="num">${fmt(i.net)} ج.م</b></div></div>`;
  const charges = (i.shipping||i.other_charges||i.discount)
    ? `<div class="inv-charges">إجمالي الأصناف: <span class="num">${fmt(i.items_total)}</span>${i.shipping?' + شحن <span class="num">'+fmt(i.shipping)+'</span>':''}${i.other_charges?' + مصاريف أخرى <span class="num">'+fmt(i.other_charges)+'</span>':''}${i.discount?' − خصم <span class="num">'+fmt(i.discount)+'</span>':''}</div>` : '';
  const qrText = `${i.company.company_name||'NIXT FOR LEATHER'} | ${baseTitle} رقم ${i.number||('#'+i.id)} | التاريخ ${i.date} | ${partyLabel}: ${i.customer?i.customer.name:''} | الصافي ${fmt(i.net)} ج.م`;
  let qrSvg = '';
  try{
    const qr = qrcode(0,'M'); qr.addData(qrText); qr.make();
    qrSvg = qr.createSvgTag({cellSize:3, margin:1});
  }catch(e){}
  showPrint(`<div class="doc invdoc">
    <div class="inv-head">
      <div class="inv-brand">
        <img src="/static/logo.png" alt="NIXT">
        <div><b>${esc(i.company.company_name||'')}</b><div class="co">${i.company.company_phone?'تليفون: <span class="num">'+esc(i.company.company_phone)+'</span>':''}${i.company.company_address?' — '+esc(i.company.company_address):''}</div></div>
      </div>
      <div class="inv-title"><b>${title}</b><span>${subtitle}</span></div>
      <div class="inv-meta">
        <span class="inv-num">رقم ${esc(i.number||('#'+i.id))}</span>
        <span class="inv-date">تاريخ: <span class="num">${i.date}</span></span>
      </div>
    </div>
    <div class="inv-cards">${cards}</div>
    <table class="inv-table"><thead>${thead}</thead><tbody>${rows}</tbody></table>
    ${totalBand}
    ${charges}
    <div class="inv-balance">
      <div><b>${isSup?'الرصيد الإجمالي المستحق للمورد':'الرصيد الإجمالي المستحق على العميل'} (بعد هذه الفاتورة)</b>
      <div class="s">يشمل إجمالي الفواتير الحالية بالإضافة إلى كافة الحسابات والدفعات والمرتجعات السابقة</div></div>
      <div class="inv-bal-amt ${bal>0?'due':'ok'}"><span>${bal>0?'مدين':'لا يوجد رصيد'}</span><b class="num">${fmt(Math.abs(bal))} ج.م</b><small>${bal>0?(isSup?'مستحق للمورد':'مستحق على '+partyLabel):''}</small></div>
    </div>
    ${i.notes?'<div class="inv-notes">ملاحظات: '+esc(i.notes)+'</div>':''}
    <div class="inv-closing"><div class="inv-foot">
      <div class="inv-qr">${qrSvg}</div>
      <div class="inv-sig"><span>توقيع المستلم / ${partyLabel}<br>........................</span><span>توقيع المحاسب / المبيعات<br>........................</span></div>
    </div>
    <div class="inv-legal">وثيقة صادرة من نظام نيكست للحسابات — ${esc(i.company.company_name||'')}</div></div>
  </div>`, true);
}

/* ---------- payments ---------- */
function onPaymentParty(){
  const c = customers.find(x=>x.id==v('p-customer'));
  document.getElementById('p-dir-label').textContent =
    c && c.kind==='supplier' ? 'دفع للمورد' : c ? 'قبض من العميل' : '';
}
async function loadPayments(){
  await refreshCustomers();
  const pays=await api('/api/payments');
  document.querySelector('#pay-table tbody').innerHTML=pays.slice(0,80).map(p=>`<tr>
    <td class="num">${p.date}</td><td>${esc(p.customer_name)}</td>
    <td>${p.party_kind==='supplier'?'دفع لمورد':'قبض'}</td>
    <td class="num">${fmt(p.cash)}</td><td class="num">${fmt(p.card)}</td><td class="num">${fmt(p.transfer)}</td><td class="num">${fmt(p.vcash)}</td><td class="num">${fmt(p.cheque)}</td>
    <td class="num"><b>${fmt(p.total)}</b></td><td>${esc(p.notes)}</td>
    <td style="white-space:nowrap"><button class="small ghost" onclick="printVoucher(${p.id})">سند</button><button class="small danger" ${canDeleteDocs()?'':'style="display:none"'} onclick="delPayment(${p.id})">حذف</button></td></tr>`).join('');
  filterTable('pay-table', document.getElementById('pay-search').value);
}
async function savePayment(){
  const cid=v('p-customer'); if(!cid){toast('اختر الطرف أولاً',true);return;}
  const body={customer_id:+cid,date:v('p-date')||today(),cash:v('p-cash')||0,card:v('p-card')||0,transfer:v('p-transfer')||0,vcash:v('p-vcash')||0,cheque:v('p-cheque')||0,notes:v('p-notes'),
    cheque_due:v('p-cheque-due'),cheque_bank:v('p-cheque-bank'),cheque_number:v('p-cheque-number')};
  if(!(+body.cash||+body.card||+body.transfer||+body.vcash||+body.cheque)){toast('اكتب مبلغ الدفعة',true);return;}
  if(+body.cheque&&!body.cheque_due){toast('اكتب تاريخ استحقاق الشيك',true);return;}
  const payAge=Math.round((Date.parse(today())-Date.parse(body.date))/864e5);
  if(payAge>30 && !confirm('تنبيه: تاريخ الدفعة قديم ('+body.date+' - من '+payAge+' يوم).\nتحفظ برضه؟')) return;
  try{
    await api('/api/payments',{method:'POST',body:JSON.stringify(body)});
    toast('تم حفظ الدفعة');
    document.getElementById('payment-entry-overlay').style.display='none';
    for(const id of ['p-cash','p-card','p-transfer','p-vcash','p-cheque']) document.getElementById(id).value=0;
    for(const id of ['p-cheque-due','p-cheque-bank','p-cheque-number']) document.getElementById(id).value='';
    document.getElementById('p-notes').value='';
    loadPayments();
  }catch(e){toast(e.message,true);}
}
async function delPayment(id, tok, confirmed){
  if(!confirmed && !confirm('حذف الدفعة رقم '+id+'؟')) return;
  try{
    await api('/api/payments/'+id,{method:'DELETE',headers:tok?{'X-Approval':tok}:{}});
    toast('تم الحذف'); loadPayments();
  }catch(e){
    if(e.need_approval){ const t=await askManagerPin('حذف دفعة محتاج موافقة المدير.'); if(t) delPayment(id,t,true); return; }
    toast(e.message,true);
  }
}

/* ---------- statement ---------- */
let lastStatement=null;
function openStatement(cid){ showTab('statement'); document.getElementById('s-customer').value=cid; loadStatement(); }
const ST_TYPE={sale:'فاتورة بيع',return:'مرتجع بيع',purchase:'فاتورة شراء',purchase_return:'مرتجع شراء',payment:'دفعة',ledger:'قيد',opening:'افتتاحي',carried:'رصيد مرحّل'};
function stGroup(t){ if(t==='sale'||t==='purchase')return'inv'; if(t==='return'||t==='purchase_return')return'ret'; if(t==='payment')return'pay'; return''; }
async function loadStatement(){
  const cid=v('s-customer'); if(!cid) return;
  let url='/api/statement?customer_id='+cid;
  if(v('s-from')) url+='&from='+v('s-from');
  if(v('s-to')) url+='&to='+v('s-to');
  const s=await api(url);
  lastStatement=s;
  const isSup = s.customer && s.customer.kind==='supplier';
  document.getElementById('s-cards').innerHTML=[
    [isSup?'المشتريات':'المبيعات',fmt(s.summary.sales)],[isSup?'المدفوع للمورد':'المحصل',fmt(s.summary.paid)],
    ['المرتجعات',fmt(s.summary.returns)],[isSup?'مستحق له':'المتبقي عليه',fmt(s.summary.remaining)],
    ['الأزواج',fmt(s.summary.pairs)],
  ].map(([t,x])=>`<div class="card"><div class="t">${t}</div><div class="v num">${x}</div></div>`).join('');
  renderStatementLines();
}
function renderStatementLines(){
  if(!lastStatement) return;
  const g=document.getElementById('s-ftype').value;
  lastStatement.lines.forEach((l,i)=>{ l._grp=stGroup(l.type); });
  document.querySelector('#s-table tbody').innerHTML=lastStatement.lines.map((l,i)=>{
    if(g && l._grp!==g) return '';
    return `<tr>
    <td><input type="checkbox" class="s-chk" data-i="${i}"></td>
    <td class="num">${l.date}</td><td class="muted" style="font-size:12px">${ST_TYPE[l.type]||esc(l.type)}</td><td>${esc(l.desc)}</td>
    <td class="num">${l.debit?fmt(l.debit):''}</td><td class="num">${l.credit?fmt(l.credit):''}</td>
    <td class="num ${l.balance>0?'bad':'ok'}"><b>${fmt(l.balance)}</b></td></tr>`;
  }).join('');
}
function toggleAllStatement(on){
  document.querySelectorAll('.s-chk').forEach(c=>c.checked=on);
}
function printStatement(onlySelected){
  if(!lastStatement){toast('اختر الطرف أولاً',true);return;}
  const s=lastStatement;
  let idx;
  if(onlySelected){
    idx=[...document.querySelectorAll('.s-chk')].filter(c=>c.checked).map(c=>+c.dataset.i);
    if(!idx.length){toast('حدّد سطور الأول بعلامة الصح',true);return;}
  } else {
    const g=document.getElementById('s-ftype').value;
    idx=s.lines.map((l,i)=>i).filter(i=>!g||s.lines[i]._grp===g||stGroup(s.lines[i].type)===g);
  }
  const lines=idx.map(i=>s.lines[i]);
  const isSup = s.customer && s.customer.kind==='supplier';
  const rows=lines.map(l=>`<tr><td class="num">${l.date}</td><td>${esc(l.desc)}</td>
    <td class="num">${l.debit?fmt(l.debit):''}</td><td class="num">${l.credit?fmt(l.credit):''}</td>
    <td class="num"><b>${fmt(l.balance)}</b></td></tr>`).join('');
  const period=(v('s-from')||v('s-to'))?` - الفترة من ${v('s-from')||'البداية'} إلى ${v('s-to')||'النهاردة'}`:'';
  showPrint(`<div class="doc">
    ${companyHeader(s.company||{}, 'كشف حساب')}
    <div class="sub">${isSup?'المورد':'العميل'}: <b>${esc(s.customer?s.customer.name:'')}</b> — بتاريخ <span class="num">${today()}</span>${period}${onlySelected?' - سطور مختارة':''}</div>
    <table><thead><tr><th>التاريخ</th><th>البيان</th><th>مدين</th><th>دائن</th><th>الرصيد</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <table class="totals" style="width:60%;margin-right:auto">
      <tr><td>${isSup?'إجمالي المشتريات':'إجمالي المبيعات'}</td><td class="num">${fmt(s.summary.sales)}</td></tr>
      <tr><td>المرتجعات</td><td class="num">-${fmt(s.summary.returns)}</td></tr>
      <tr><td>${isSup?'المدفوع للمورد':'المحصل'}</td><td class="num">-${fmt(s.summary.paid)}</td></tr>
      <tr><td><b>${isSup?'مستحق له':'المتبقي عليه'}</b></td><td class="num"><b>${fmt(s.summary.remaining)}</b></td></tr>
    </table>
  </div>`);
}
function showPrint(html, preview){
  if(preview){
    document.getElementById('print-preview-body').innerHTML = html;
    openModal('print-preview');
    return;
  }
  document.getElementById('print-area').innerHTML=html;
  setTimeout(()=>window.print(),150);
}
function doPrintFromPreview(){
  document.getElementById('print-area').innerHTML = document.getElementById('print-preview-body').innerHTML;
  setTimeout(()=>window.print(),150);
}

/* ---------- import ---------- */
function readFile(file, asB64){
  return new Promise((res,rej)=>{
    const r=new FileReader();
    r.onload=()=>res(r.result); r.onerror=rej;
    if(asB64) r.readAsDataURL(file); else r.readAsText(file,'utf-8');
  });
}
let impTable=null, impFields=null, impB64=null, impText=null, impFileName='';
async function impPreview(){
  const f=document.getElementById('imp-file').files[0];
  if(!f){toast('اختر ملف',true);return;}
  impFileName=f.name;
  try{
    let body;
    if(/\.xlsx$/i.test(f.name)){
      const dataUrl=await readFile(f,true);
      impB64=dataUrl.split(',')[1]; impText=null;
      body={mode:'preview',kind:document.getElementById('imp-kind').value,b64:impB64};
    }else{
      impText=await readFile(f); impB64=null;
      body={mode:'preview',kind:document.getElementById('imp-kind').value,text:impText};
    }
    const r=await api('/api/import/parties',{method:'POST',body:JSON.stringify(body)});
    impFields=r.fields;
    const mapDiv=document.getElementById('imp-map');
    const headOpts=(sel)=> '<option value="-1">-- تجاهل --</option>'+r.headers.map((h,i)=>`<option value="${i}" ${r.suggested&&r.suggested[sel]===i?'selected':''}>${esc(h||('عمود '+(i+1)))}</option>`).join('');
    mapDiv.innerHTML='<h3 style="margin:6px 0">عيّن الأعمدة ('+r.rows+' صف)</h3>'+
      Object.keys(r.fields).map(f=>`<div class="field" style="display:inline-block;margin:4px"><label>${r.fields[f]}</label><select id="map-${f}">${headOpts(f)}</select></div>`).join('')+
      '<div style="margin-top:8px"><button onclick="impRun()">تنفيذ الاستيراد</button></div>'+
      '<div class="muted" style="margin-top:6px">عينة: '+r.sample.slice(0,3).map(row=>esc(row.join(' | '))).join('<br>')+'</div>';
    mapDiv.classList.remove('hidden');
  }catch(e){ document.getElementById('imp-result').textContent='فشل: '+e.message; }
}
async function impRun(){
  const mapping={};
  for(const f of Object.keys(impFields)){
    const val=parseInt(document.getElementById('map-'+f).value);
    if(val>=0) mapping[f]=val;
  }
  if(mapping.name===undefined){toast('حدد عمود الاسم',true);return;}
  try{
    const body={mode:'commit',kind:document.getElementById('imp-kind').value,mapping,filename:impFileName};
    if(impB64) body.b64=impB64; else body.text=impText;
    const r=await api('/api/import/parties',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('imp-result').innerHTML=
      `تم: جديد <b class="num">${r.added}</b>، تحديث رصيد <b class="num">${r.updated}</b>، تم تخطيه <span class="num">${r.skipped}</span>`;
    document.getElementById('imp-map').classList.add('hidden');
    refreshCustomers();
  }catch(e){ document.getElementById('imp-result').textContent='فشل: '+e.message; }
}

/* ---------- settings & users ---------- */
async function loadMobileConfig(){
  try{
    const c=await api('/api/mobile/config');
    document.getElementById('mob-enabled').checked=!!c.enabled;
    document.getElementById('mob-body').classList.toggle('hidden',!c.enabled);
    const st=document.getElementById('mob-status');
    st.textContent=c.enabled?(c.active?'— شغال دلوقتي':'— مفعّل، يشتغل بعد إعادة تشغيل البرنامج'):'— مقفول';
    document.getElementById('mob-restart').textContent=(c.enabled&&!c.active)?'التفعيل محتاج إعادة تشغيل البرنامج على الكمبيوتر عشان يبدأ يسمع على الشبكة.':'';
    document.getElementById('mob-key').value=c.key||'';
    const box=document.getElementById('mob-urls'); box.innerHTML='';
    (c.urls||[]).forEach(u=>{
      const d=document.createElement('div');
      d.style.textAlign='center';
      let qrSvg='';
      try{const qr=qrcode(0,'M');qr.addData(u.url);qr.make();qrSvg=qr.createSvgTag({cellSize:4,margin:1});}catch(e){}
      d.innerHTML='<div style="background:#fff;border-radius:10px;padding:8px;display:inline-block">'+qrSvg+'</div>'+
        '<div style="margin-top:6px;font-weight:700;font-size:12.5px">'+(u.tailscale?'من بره البيت (Tailscale)':'نفس الواي فاي')+(u.recommended?' <span style="color:#166534;font-size:11px">(الأفضل - امسح ده)</span>':'')+'</div>'+
        '<div class="muted num" style="font-size:11px">'+esc(u.ip)+':'+c.port+'</div>';
      box.appendChild(d);
    });
    if(!(c.urls||[]).length)box.innerHTML='<span class="muted">مفيش عنوان شبكة متاح — تأكد إن الكمبيوتر متصل بالشبكة.</span>';
  }catch(e){}
}
async function mobToggle(){
  const en=document.getElementById('mob-enabled').checked;
  try{
    await api('/api/mobile/config',{method:'POST',body:JSON.stringify({enabled:en})});
    toast(en?'اتفعل الوصول من الموبايل':'اتقفل الوصول من الموبايل');
    loadMobileConfig();
  }catch(e){toast(e.message,true);document.getElementById('mob-enabled').checked=!en;}
}
async function mobRegen(){
  if(!confirm('تجديد المفتاح هيقفل أي موبايل متصل دلوقتي. متأكد؟'))return;
  try{
    await api('/api/mobile/config',{method:'POST',body:JSON.stringify({regenerate:true})});
    toast('اتجدد المفتاح');loadMobileConfig();
  }catch(e){toast(e.message,true);}
}
async function drvRefresh(){
  try{
    const st=await api('/api/drive/status');
    const panel=document.getElementById('drv-panel');
    if(!st.configured){panel.style.display='none';return;}
    panel.style.display='';
    const el=document.getElementById('drv-status');
    let txt=st.connected?'مربوط — آخر رفع ناجح: '+(st.last_ok||'لسه'):'غير مربوط';
    if(st.pending)txt+=' — فيه تعديلات مستنية الرفع';
    if(st.last_error)txt+=' — آخر خطأ: '+st.last_error;
    el.textContent=txt;
    document.getElementById('drv-connect').style.display=st.connected?'none':'';
    document.getElementById('drv-upload').style.display=st.connected?'':'none';
    document.getElementById('drv-disconnect').style.display=st.connected?'':'none';
  }catch(e){}
}
async function drvConnect(){
  try{
    const r=await api('/api/drive/connect',{method:'POST',body:'{}'});
    toast('المتصفح اتفتح — اختار حسابك ووافق، وبعدين ارجع هنا');
    setTimeout(drvRefresh,4000);setTimeout(drvRefresh,10000);
  }catch(e){toast(e.message,true);}
}
async function drvDisconnect(){
  if(!confirm('فصل جوجل درايف؟ الموبايل مش هيعرض نسخة محدثة وهو بره.'))return;
  try{
    await api('/api/drive/disconnect',{method:'POST',body:'{}'});
    toast('اتفصل الربط');drvRefresh();
  }catch(e){toast(e.message,true);}
}
async function drvUploadNow(){
  try{
    await api('/api/drive/upload-now',{method:'POST',body:'{}'});
    toast('اترفعت النسخة');drvRefresh();
  }catch(e){toast(e.message,true);}
}
async function loadSettings(){
  const s=await api('/api/settings');
  document.getElementById('set-name').value=s.company_name||'';
  document.getElementById('set-phone').value=s.company_phone||'';
  document.getElementById('set-address').value=s.company_address||'';
  document.getElementById('set-alert-days').value=s.alert_days||30;
  document.getElementById('set-backup-dir').value=s.backup_dir||'';
  document.getElementById('set-backup-info').textContent='النسخ الاحتياطي الآن يذهب إلى: '+s.backup_dir_effective;
  document.getElementById('set-login-users').checked = (s.login_show_users!=='0');
  loadShortcutStatus();
  document.getElementById('set-idle-min').value=s.idle_lock_minutes||'5';
  document.getElementById('set-divisor').value=s.wholesale_divisor||'1.22';
  wholesaleDivisor=parseFloat(s.wholesale_divisor)||1.22;
  loadMobileConfig(); drvRefresh();
  loadUsers(); loadActivity();
}
async function loadActivity(){
  try{
    const list=await api('/api/activity');
    document.querySelector('#act-table tbody').innerHTML=list.map(a=>`<tr>
      <td class="num">${esc(a.ts)}</td><td>${esc(a.username)}</td><td>${esc(a.action)}</td><td>${esc(a.details)}</td></tr>`).join('');
  }catch(e){}
}
async function saveSettings(){
  try{
    await api('/api/settings',{method:'POST',body:JSON.stringify({
      company_name:v('set-name'),company_phone:v('set-phone'),company_address:v('set-address'),
      alert_days:v('set-alert-days')||'30',backup_dir:v('set-backup-dir'),login_show_users:document.getElementById('set-login-users').checked?'1':'0',
      wholesale_divisor:v('set-divisor')||'1.22',idle_lock_minutes:v('set-idle-min')||'5'})});
    toast('تم حفظ الإعدادات'); loadSettings();
  }catch(e){toast(e.message,true);}
}
async function loadUsers(){
  const us=await api('/api/users');
  const g=document.getElementById('users-grid');
  g.innerHTML=us.map(u=>{
    const badges=[];
    if(u.disabled) badges.push('<span class="badge bad">موقوف</span>');
    else if(u.locked) badges.push('<span class="badge warn">متقفل مؤقتًا</span>');
    else badges.push('<span class="badge ok">نشط</span>');
    if(u.must_change) badges.push('<span class="badge info">لازم يغير كلمة السر</span>');
    if(u.has_pin) badges.push('<span class="badge">PIN</span>');
    if(u.perms) badges.push('<span class="badge info">صلاحيات مخصصة</span>');
    const meta=[u.last_login?esc(fmtLast(u.last_login)):'لسه مدخلش', u.last_action?('آخر حاجة عملها: '+esc(u.last_action)):''].filter(Boolean).join('<br>');
    const initials=esc((u.username.split(' ').slice(0,2).map(w=>w[0]).join('')||u.username[0]||'').toUpperCase());
    return `<div class="ucard">
      <div class="uhead">
        <div class="avatar">${initials}</div>
        <div style="flex:1;min-width:0">
          <div class="uname">${esc(u.username)}</div>
          <div class="umeta">${meta}</div>
        </div>
      </div>
      <div class="row" style="gap:6px;flex-wrap:wrap">${badges.join(' ')}</div>
      <div class="row" style="margin-top:8px;align-items:center">
        <label class="muted" style="font-size:12px">الدور:</label>
        <select onchange="setUserRole(${u.id},this.value)">
          ${['user','accountant','warehouse','admin'].map(r=>`<option value="${r}"${u.role===r?' selected':''}>${roleLabel(r)}</option>`).join('')}
        </select>
      </div>
      <div class="uacts">
        <button class="small ghost" onclick="resetUser(${u.id},'${esc(u.username)}')">كلمة السر</button>
        <button class="small ghost" onclick="openPinModal(${u.id},'${esc(u.username)}',${u.has_pin?1:0})">الرقم السري</button>
        <button class="small ghost" onclick="openPerms(${u.id},'${esc(u.username)}')">صلاحيات</button>
        <button class="small ghost" onclick="showUserActivity('${esc(u.username)}')">النشاط</button>
        ${u.locked?`<button class="small ghost" onclick="unlockUser(${u.id})">فك القفل</button>`:''}
        <button class="small ${u.disabled?'ghost':'danger'}" onclick="toggleUser(${u.id},${u.disabled?0:1})">${u.disabled?'تفعيل':'إيقاف'}</button>
        <button class="small danger" onclick="delUser(${u.id})">حذف</button>
      </div>
    </div>`;
  }).join('');
}
async function setUserRole(id, role){
  try{ await api('/api/users/'+id+'/role',{method:'POST',body:JSON.stringify({role})}); toast('تم تغيير الدور'); loadUsers(); }
  catch(e){ toast(e.message,true); loadUsers(); }
}
let pinUserId=null;
function openPinModal(id,name,has){
  pinUserId=id;
  document.getElementById('pin-title').textContent='الرقم السري (PIN): '+name;
  document.getElementById('pin-value').value='';
  document.getElementById('pin-hint').textContent=has?'ليه رقم سري حاليًا. اكتب رقم جديد لتغييره، أو دوس "مسح الرقم" لإلغائه.':'رقم من 4 لـ 8 أرقام للدخول السريع من الموبايل. بيتخزن مشفر زي كلمة السر بالظبط.';
  openModal('pin-modal');
}
async function savePin(){
  try{
    await api('/api/users/'+pinUserId+'/pin',{method:'POST',body:JSON.stringify({pin:document.getElementById('pin-value').value.trim()})});
    toast('تم حفظ الرقم السري'); closeModal('pin-modal'); loadUsers();
  }catch(e){ toast(e.message,true); }
}
async function clearPin(){
  try{
    await api('/api/users/'+pinUserId+'/pin',{method:'POST',body:JSON.stringify({pin:''})});
    toast('تم مسح الرقم السري'); closeModal('pin-modal'); loadUsers();
  }catch(e){ toast(e.message,true); }
}
let permsUserId=null;
function openPerms(id,name){
  permsUserId=id;
  api('/api/users').then(us=>{
    const u=us.find(x=>x.id===id);
    document.getElementById('perms-who').textContent=name;
    document.getElementById('perms-template').value='';
    const secs=(u && u.perms && u.perms.sections)||['accounts','inventory','production','settings'];
    document.querySelectorAll('#perms-sections input').forEach(c=>c.checked=secs.indexOf(c.dataset.sec)>=0);
    document.getElementById('perms-nodelete').checked=!!(u && u.perms && u.perms.ops && u.perms.ops.indexOf('delete_docs')>=0);
    openModal('perms-modal');
  });
}
async function savePerms(){
  const secs=[...document.querySelectorAll('#perms-sections input')].filter(c=>c.checked).map(c=>c.dataset.sec);
  const ops=document.getElementById('perms-nodelete').checked?['delete_docs']:[];
  const body=(secs.length===4 && !ops.length)?{perms:null}:{perms:{sections:secs,ops}};
  try{ await api('/api/users/'+permsUserId+'/perms',{method:'POST',body:JSON.stringify(body)}); toast('تم حفظ الصلاحيات'); closeModal('perms-modal'); loadUsers(); }
  catch(e){ toast(e.message,true); }
}
function resetPermsDefault(){
  document.querySelectorAll('#perms-sections input').forEach(c=>c.checked=true);
  document.getElementById('perms-nodelete').checked=false;
}
async function toggleUser(id, dis){
  try{ await api('/api/users/'+id+'/status',{method:'POST',body:JSON.stringify({disabled:!!dis})}); toast(dis?'تم إيقاف المستخدم':'تم تفعيل المستخدم'); loadUsers(); }
  catch(e){ toast(e.message,true); }
}
async function unlockUser(id){
  try{ await api('/api/users/'+id+'/unlock',{method:'POST',body:'{}'}); toast('اتفك القفل المؤقت'); loadUsers(); }
  catch(e){ toast(e.message,true); }
}
async function showUserActivity(name){
  document.getElementById('uact-who').textContent=name;
  const box=document.getElementById('uact-list');
  box.innerHTML='<p class="muted">جارٍ التحميل...</p>';
  openModal('uact-modal');
  try{
    const list=await api('/api/activity');
    const mine=list.filter(a=>a.username===name).slice(0,50);
    box.innerHTML=mine.length?mine.map(a=>`<div style="padding:6px 0;border-bottom:1px solid var(--line);font-size:13px"><span class="muted num" style="font-size:12px">${esc(a.ts)}</span> - ${esc(a.action)}${a.details?' · '+esc(a.details):''}</div>`).join(''):'<p class="muted">مفيش نشاط مسجل له لسه.</p>';
  }catch(e){ box.innerHTML='<p class="bad">'+esc(e.message)+'</p>'; }
}
function applyPermTemplate(){
  const t=document.getElementById('perms-template').value; if(!t) return;
  const map={full:{s:['accounts','inventory','production','settings'],d:false},
    full_nodel:{s:['accounts','inventory','production','settings'],d:true},
    accounts:{s:['accounts'],d:false},
    accounts_nodel:{s:['accounts'],d:true},
    store:{s:['inventory','production'],d:true}};
  const m=map[t]; if(!m) return;
  document.querySelectorAll('#perms-sections input').forEach(c=>c.checked=m.s.indexOf(c.dataset.sec)>=0);
  document.getElementById('perms-nodelete').checked=m.d;
}
function openSessions(){ openModal('sessions-modal'); loadSessions(); }
async function loadSessions(){
  const box=document.getElementById('sessions-list');
  box.innerHTML='<p class="muted">جارٍ التحميل...</p>';
  try{
    const ss=await api('/api/sessions');
    box.innerHTML=ss.length?ss.map(s=>`<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px">
      <div style="flex:1;min-width:0"><b>${esc(s.username)}</b> <span class="muted">${esc(roleLabel(s.role))}${s.device?' · '+esc(s.device):''}</span> ${s.current?'<span class="badge ok">الجلسة دي</span>':''}<br>
      <span class="muted num" style="font-size:12px">دخل: ${esc((s.created||'').slice(5,16))} · آخر نشاط: ${esc((s.last||'').slice(5,16))}</span></div>
      ${s.current?'':'<button class="small danger" data-sid="'+s.sid+'" onclick="kickSession(this.dataset.sid)">طرد</button>'}
    </div>`).join(''):'<p class="muted">مفيش جلسات نشطة.</p>';
  }catch(e){ box.innerHTML='<p class="bad">'+esc(e.message)+'</p>'; }
}
async function kickSession(sid){
  if(!confirm('طرد الجلسة دي؟ هيتطلب من الشخص يدخل من الأول.')) return;
  try{ await api('/api/sessions/kick',{method:'POST',body:JSON.stringify({sid})}); toast('اتطردت الجلسة'); loadSessions(); }
  catch(e){ toast(e.message,true); loadSessions(); }
}
function openDailyReport(){ openModal('report-modal'); loadDailyReport(); }
async function loadDailyReport(){
  const box=document.getElementById('report-list');
  box.innerHTML='<p class="muted">جارٍ التحميل...</p>';
  try{
    const list=await api('/api/activity?date='+today());
    const by={};
    list.forEach(a=>{ const u=a.username||'غير معروف'; (by[u]=by[u]||[]).push(a); });
    const names=Object.keys(by);
    box.innerHTML=names.length?names.map(u=>`<div style="margin-bottom:12px"><div style="font-weight:700;font-size:14px">${esc(u)} <span class="badge info">${by[u].length} حركة</span></div>`+
      by[u].slice(0,30).map(a=>`<div style="padding:3px 0;font-size:13px"><span class="muted num" style="font-size:12px">${esc((a.ts||'').slice(11,16))}</span> - ${esc(a.action)}${a.details?' · '+esc(a.details):''}</div>`).join('')+'</div>').join(''):'<p class="muted">مفيش نشاط مسجل النهارده لسه.</p>';
  }catch(e){ box.innerHTML='<p class="bad">'+esc(e.message)+'</p>'; }
}
let approvalResolve=null;
function askManagerPin(why){
  return new Promise(res=>{
    approvalResolve=res;
    document.getElementById('approve-why').textContent=why||'الإجراء ده محتاج موافقة المدير.';
    document.getElementById('approve-pin').value='';
    document.getElementById('approve-err').textContent='';
    openModal('approve-modal');
    setTimeout(()=>document.getElementById('approve-pin').focus(),100);
  });
}
async function submitApproval(){
  const pin=document.getElementById('approve-pin').value.trim();
  if(!pin){ document.getElementById('approve-err').textContent='اكتب الرقم السري'; return; }
  try{
    const j=await api('/api/approve',{method:'POST',body:JSON.stringify({pin})});
    closeModal('approve-modal');
    toast('المدير '+j.by+' وافق');
    const r=approvalResolve; approvalResolve=null; if(r) r(j.approval);
  }catch(e){ document.getElementById('approve-err').textContent=e.message; }
}
function cancelApproval(){ closeModal('approve-modal'); const r=approvalResolve; approvalResolve=null; if(r) r(null); }
function openUserModal(){
  document.getElementById('u-name').value=''; document.getElementById('u-pass').value=''; document.getElementById('u-pin').value='';
  openModal('user-modal');
}
async function addUser(){
  try{
    await api('/api/users',{method:'POST',body:JSON.stringify({username:v('u-name'),password:document.getElementById('u-pass').value,role:document.getElementById('u-role').value,pin:document.getElementById('u-pin').value.trim()})});
    toast('تمت الإضافة');
    document.getElementById('u-name').value='';document.getElementById('u-pass').value='';document.getElementById('u-pin').value='';
    closeModal('user-modal');
    loadUsers();
  }catch(e){toast(e.message,true);}
}
async function resetUser(id,name){
  const pw=prompt('كلمة سر مؤقتة جديدة للمستخدم '+name+' (هيتحط عليه يغيرها أول ما يدخل):');
  if(!pw) return;
  try{ await api('/api/users/'+id+'/reset',{method:'POST',body:JSON.stringify({password:pw})}); toast('تم التصفير - هيتطلب منه يغيرها عند الدخول'); }
  catch(e){toast(e.message,true);}
}
async function delUser(id){
  if(!confirm('حذف المستخدم؟')) return;
  try{ await api('/api/users/'+id,{method:'DELETE'}); toast('تم الحذف'); loadUsers(); }
  catch(e){toast(e.message,true);}
}
async function changePassword(){
  try{
    await api('/api/change-password',{method:'POST',body:JSON.stringify({old:document.getElementById('pw-old').value,new:document.getElementById('pw-new').value})});
    toast('تم تغيير كلمة السر');
    document.getElementById('pw-old').value='';document.getElementById('pw-new').value='';
  }catch(e){toast(e.message,true);}
}

/* ---------- backup & update ---------- */
async function doBackup(){
  try{ await api('/api/backup',{method:'POST',body:'{}'});
    toast('تم حفظ نسخة احتياطية'); }
  catch(e){ toast(e.message,true); }
}
async function openUpdate(){
  document.getElementById('update-modal').style.display='flex';
  document.getElementById('update-status').textContent='جارٍ الفحص...';
  document.getElementById('update-btn').classList.add('hidden');
  try{
    const r=await api('/api/check-update');
    const st=document.getElementById('update-status');
    if(!r.configured){ st.textContent='التحديث التلقائي غير مفعّل بعد.'; return; }
    if(!r.online){
      st.innerHTML=esc(r.message||'تعذر الاتصال بخادم التحديثات.')+' الإصدار الحالي: <span class="num">'+esc(r.current)+'</span>'
        +(r.error?'<br><small class="muted" style="font-size:11px;direction:ltr;display:inline-block">'+esc(r.error)+'</small>':'')
        +'<br><button class="ghost" style="margin-top:8px" onclick="openUpdate()">إعادة المحاولة</button>';
      return;
    }
    if(r.update_available){
      st.innerHTML=`يوجد إصدار جديد: <b class="num">${r.latest}</b> (الحالي: <span class="num">${r.current}</span>)`+(r.notes?'<br>'+esc(r.notes):'');
      if(r.has_zip) document.getElementById('update-btn').classList.remove('hidden');
    }else{
      st.textContent='النظام محدث. الإصدار الحالي: '+r.current;
    }
  }catch(e){ document.getElementById('update-status').textContent='تعذر الفحص: '+e.message; }
}
async function runUpdate(){
  document.getElementById('update-status').textContent='جارٍ عمل نسخة احتياطية وتحميل التحديث وتثبيته... لا تغلق البرنامج.';
  document.getElementById('update-btn').classList.add('hidden');
  try{
    const stages={checking:'فحص الإصدار',backup:'نسخة أمان',download:'تحميل',extract:'فك الملفات',handoff:'بدء التثبيت',error:'خطأ'};
    const progress=setInterval(async()=>{try{const p=await api('/api/update-progress');document.getElementById('update-status').textContent=(stages[p.stage]||p.stage)+(p.stage==='download'?' '+Math.round(p.downloaded/1024)+' KB':'')+(p.error?' : '+p.error:'');}catch(e){}},1000);
    let r;try{r=await api('/api/do-update',{method:'POST',body:'{}'});}finally{clearInterval(progress);}
    window.nixtExpectedUpdate=r.version;
    document.getElementById('update-status').innerHTML=
      'تم التحميل. البرنامج بيبدأ تثبيت الملفات وإعادة التشغيل. لو ظهرت مشكلة هنقولك هنا. بياناتك محفوظة واتعمل لها نسخة احتياطية قبل التحديث.';
  }catch(e){ document.getElementById('update-status').textContent='فشل التحديث: '+e.message; return; }
  const t0=Date.now(); let sawDown=false;
  const iv=setInterval(async()=>{
    try{
      const rv=await api('/api/version');
      if(rv.version===window.nixtExpectedUpdate){
        clearInterval(iv);
        document.getElementById('update-status').innerHTML='تم التحديث للنسخة <b class="num">'+esc(rv.version||'')+'</b> - بيعاد تحميل البرنامج...';
        setTimeout(()=>location.reload(),1200);
      }
    }catch(e){
      sawDown=true;
    }
      if(Date.now()-t0>90000){
        clearInterval(iv);
        document.getElementById('update-status').innerHTML='التحديث اتأخر أكتر من المعتاد. اقفل النافذة وشغّل البرنامج من START.bat في فولدر البرنامج. لو المشكلة فضلت: ابعت ملف update-log.txt من فولدر بيانات البرنامج (AppData\\NixtAccounts).';
      }
  },3000);
}
function closeUpdate(){ document.getElementById('update-modal').style.display='none'; }

async function wipeData(){
  const c=document.getElementById('wipe-confirm').value.trim();
  if(c!=='تصفير'){ toast('اكتب كلمة تصفير في الخانة الأول', true); return; }
  const out=document.getElementById('wipe-result');
  out.textContent='جارٍ أخذ نسخة احتياطية ثم التصفير...';
  try{
    const r=await api('/api/wipe',{method:'POST',body:JSON.stringify({confirm:c})});
    out.innerHTML='تم التصفير. النسخة الاحتياطية محفوظة في: <span class="num">'+esc(r.backup)+'</span>';
    document.getElementById('wipe-confirm').value='';
    refreshCustomers();
  }catch(e){ out.textContent='فشل: '+e.message; }
}
/* ---------- sync ---------- */
const SYNC_STATES={not_configured:'الفولدر مش متظبط لسه',dir_missing:'فولدر درايف مش موجود على الجهاز ده',local_only:'أول مرة - مفيش نسخة مرفوعة لسه',up_to_date:'البيانات محدثة على الجهازين',local_newer:'الجهاز ده فيه بيانات أحدث - ارفع نسخة',remote_newer:'فيه نسخة أحدث على جهاز تاني - اسحبها',conflict:'الجهازين اتعدلوا من آخر مزامنة - راجع قبل السحب'};
async function loadSync(){
  try{
    const r=await api('/api/sync/status');
    document.getElementById('sy-device').value=r.device||'';
    const st=await api('/api/settings');
    document.getElementById('sy-dir').value=st.sync_dir||'';
    const scolor = r.state==='up_to_date'?'#dcfce7':(r.state==='conflict'?'#fee2e2':'#e0f2fe');
    const lockTxt = r.locked_by ? ('مقفول - مفتوح على '+r.locked_by) : (r.own_lock ? 'الجهاز ده هو النشط' : ((r.lock&&r.lock.device)?('آخر قفل: '+r.lock.device) : 'مفيش قفل'));
    const lockColor = r.locked_by ? '#fee2e2' : '#dcfce7';
    document.getElementById('sy-status').innerHTML=
      `<div class="card" style="background:${scolor}"><div class="t">الحالة</div><div style="font-weight:700;margin-top:4px">${SYNC_STATES[r.state]||r.state}</div></div>`+
      `<div class="card" style="background:${lockColor}"><div class="t">قفل الأجهزة</div><div style="font-weight:700;margin-top:4px">${esc(lockTxt)}</div></div>`+
      `<div class="card"><div class="t">فولدر المزامنة</div><div class="num" style="font-size:13px;direction:ltr">${esc(r.sync_dir||'- مش محدد -')}</div></div>`+
      `<div class="card"><div class="t">آخر مزامنة</div><div class="v num" style="font-size:15px">${esc(r.last_sync||'لسه')}</div></div>`+
      `<div class="card"><div class="t">مكان البيانات على الجهاز ده</div><div class="num" style="font-size:12px;direction:ltr;margin-top:4px">${esc(r.data_dir||'')}</div></div>`;
    document.getElementById('sy-remote').innerHTML = (r.remote
      ? `آخر نسخة مرفوعة: من جهاز <b>${esc(r.remote.device||'')}</b> - <span class="num">${esc(r.remote.ts||'')}</span> - حجم ${(r.remote.size/1048576).toFixed(1)} ميجا - إصدار ${esc(r.remote.app_version||'')}`
      : 'مفيش نسخة مرفوعة لسه في الفولدر.')
      + (r.migrated_from ? `<div style="margin-top:6px;color:var(--ok)"><svg class="ic"><use href="#ic-check-circle"/></svg> بياناتك القديمة اتنقلت لوحدها من النسخة الأقدم: <span class="num" style="font-size:12px">${esc(r.migrated_from)}</span></div>` : '');
  }catch(e){toast(e.message,true);}
}
async function saveSyncSettings(){
  try{
    await api('/api/settings',{method:'POST',body:JSON.stringify({device_name:v('sy-device'),sync_dir:v('sy-dir')})});
    toast('تم الحفظ'); loadSync();
  }catch(e){toast(e.message,true);}
}
async function doPush(){
  try{
    const r=await api('/api/sync/push',{method:'POST',body:'{}'});
    toast('اترفعت نسخة - جوجل درايف هيرفعها للسحابة في الخلفية');
    loadSync();
  }catch(e){toast(e.message,true);}
}
async function doPull(){
  let msg='السحب هيدمج آخر نسخة مرفوعة مع بيانات الجهاز ده على مستوى السجل: الأحدث بيكسب والمحذوف بيفضل محذوف، ومفيش بيانات بتضيع. النظام بياخد نسخة احتياطية لوحده قبل الدمج. متأكد؟';
  if(!confirm(msg)) return;
  try{
    const r=await api('/api/sync/pull',{method:'POST',body:'{}'});
    const m=(r.meta&&r.meta.merge)||{};
    toast('تم الدمج: '+(m.inserted||0)+' سجل جديد، '+(m.updated||0)+' تحديث، '+(m.deleted||0)+' حذف');
    setTimeout(()=>location.reload(),2000);
  }catch(e){toast(e.message,true);}
}

/* ---------- sales / purchases movements & returns ---------- */
let allInvoices=[];
function newInvoiceFromSection(kind){
  showTab('invoice');
  document.getElementById('i-kind').value=kind;
  resetInvoiceForm();
  document.getElementById('i-return-of').value='';
  document.getElementById('i-return-banner').classList.add('hidden');
  onInvoiceKind();
}
async function loadMovements(kind){
  await refreshCustomers();
  allInvoices = await api('/api/invoices');
  const ret = kind==='sale'?'return':'purchase_return';
  const list = allInvoices.filter(i=>i.kind===kind||i.kind===ret);
  const tbl = kind==='sale'?'sales-table':'purchases-table';
  document.querySelector('#'+tbl+' tbody').innerHTML=list.map(i=>{
    const isRet=i.kind===ret;
    let det='';
    if(isRet){ det=esc(i.reason||''); if(i.return_of) det+=(det?' ':'')+'(مرتبطة بفاتورة #'+i.return_of+')'; }
    else if(i.notes) det=esc(i.notes);
    return `<tr>
    <td class="num">${esc(i.number||('#'+i.id))}</td>
    <td class="num">${i.date}</td>
    <td>${isRet?'<span class="bad">'+KIND_LABEL[i.kind]+'</span>':KIND_LABEL[i.kind]}</td>
    <td>${esc(i.customer_name)}</td>
    <td class="num">${isSupInvoice(i)?quantitySummary(i.items||[]):fmt(i.pairs)}</td><td class="num"><b>${fmt(i.net)}</b></td>
    <td>${det}</td>
    <td style="white-space:nowrap">
      ${!isRet?`<button class="small" title="تسجيل مرتجع من الفاتورة دي" onclick="startReturn(${i.id})"><svg class="ic"><use href="#ic-refresh-cw"/></svg> مرتجع</button>
      <button class="small ghost" title="نسخ الفاتورة دي لفاتورة جديدة" onclick="dupInvoice(${i.id})">تكرار</button>`:''}
      <button class="small ghost" onclick="openStatement(${i.customer_id})">كشف</button>
      <button class="small ghost" onclick="openPrintChoice(${i.id})">طباعة</button></td></tr>`;
  }).join('');
  filterTable(tbl, document.getElementById(kind==='sale'?'sales-search':'purchases-search').value);
}
function gearReturn(){
  const k=document.getElementById('i-kind').value;
  const base=(k==='purchase'||k==='purchase_return')?'purchase':'sale';
  const list=allInvoices.filter(i=>i.kind===base);
  if(!list.length){toast('مفيش فواتير '+(base==='sale'?'بيع':'شراء')+' لسه',true);return;}
  document.getElementById('ret-invoice').innerHTML=list.map(i=>
    `<option value="${i.id}">#${i.id} - ${i.date} - ${esc(i.customer_name)} - ${fmt(i.net)} جنيه</option>`).join('');
  document.getElementById('ret-modal').style.display='flex';
}
function applyReturn(){
  const id=+v('ret-invoice');
  document.getElementById('ret-modal').style.display='none';
  if(id) startReturn(id);
}
async function startReturn(id){
  const inv=await api('/api/invoices/'+id);
  if(!inv||!inv.id){toast('الفاتورة مش موجودة',true);return;}
  const retKind = inv.kind==='sale'?'return':inv.kind==='purchase'?'purchase_return':null;
  if(!retKind){toast('المرتجع بيتعمل من فاتورة بيع أو شراء',true);return;}
  showTab('invoice');
  document.getElementById('i-kind').value=retKind;
  onInvoiceKind();
  document.getElementById('i-customer').value=inv.customer_id;
  document.getElementById('i-date').value=today();
  document.getElementById('i-items').innerHTML='';
  (inv.items||[]).forEach(it=>addItemRow({...it,leather_packs:[]}));
  document.getElementById('i-shipping').value=0;
  document.getElementById('i-other').value=0;
  document.getElementById('i-discount').value=0;
  document.getElementById('i-notes').value='';
  document.getElementById('i-reason').value='';
  document.getElementById('i-return-of').value=inv.id;
  const bn=document.getElementById('i-return-banner');
  bn.textContent='مرتجع للفاتورة #'+inv.id+' ('+esc(inv.customer_name||'')+') - عدّل الكميات واكتب السبب واحفظ';
  bn.classList.remove('hidden');
  calcInvoice();
  toast('حمّلنا الفاتورة كمرتجع - راجع واحفظ');
}

/* ---------- treasury & cheques ---------- */
const TR_METHOD={cash:'كاش',vcash:'فودافون كاش',transfer:'تحويل بنكي',card:'شبكة',cheque:'شيك'};
let trAll=[];
async function loadTreasury(){
  const m=document.getElementById('tr-month').value;
  const r=await api('/api/treasury'+(m?'?month='+m:''));
  trAll=r.entries||[];
  const bm=r.by_method||{};
  const ch=(mkey,label)=>{const b=bm[mkey]||{in:0,out:0,net:0};return `<div class="card"><div class="t">${label}</div><div class="v num">${fmt(b.net)}</div><div class="muted num" style="font-size:11px">في ${fmt(b.in)} / من ${fmt(b.out)}</div></div>`;};
  document.getElementById('tr-cards').innerHTML=
    ch('cash','كاش')+ch('vcash','فودافون كاش')+ch('transfer','تحويل بنكي')+ch('card','شبكة')+ch('cheque','شيكات محصلة')+
    `<div class="card" style="background:#0c4a6e;color:#fff"><div class="t" style="color:#bae6fd">صافي الخزينة</div><div class="v num">${fmt(r.net)}</div><div style="font-size:11px;color:#bae6fd" class="num">قبض ${fmt(r.total_in)} / صرف ${fmt(r.total_out)}</div></div>`;
  renderTreasury();
}
function renderTreasury(){
  const fm=document.getElementById('tr-fmethod').value;
  const fk=document.getElementById('tr-fkind').value;
  const fs=document.getElementById('tr-fsrc').value;
  const q=(document.getElementById('tr-search').value||'').trim();
  const list=trAll.filter(e=>(!fm||e.method===fm)&&(!fk||e.kind===fk)&&(!fs||e.source===fs)
    &&(!q||(e.category||'').includes(q)||(e.notes||'').includes(q)||(e.party_name||'').includes(q)));
  document.querySelector('#tr-table tbody').innerHTML=list.map(e=>`<tr>
    <td class="num">${e.date}</td><td>${e.kind==='in'?'<span class="ok">قبض</span>':'<span class="bad">صرف</span>'}</td>
    <td>${TR_METHOD[e.method]||esc(e.method)}</td>
    <td>${esc(e.category)}</td><td>${esc(e.party_name||'')}</td>
    <td class="num"><b>${fmt(e.amount)}</b></td><td>${esc(e.notes)}</td>
    <td>${e.source==='manual'&&currentUser.role==='admin'?`<button class="small danger" onclick="delTreasury(${e.id})">حذف</button>`:''}</td></tr>`).join('');
}
function clearTreasuryFilters(){
  for(const id of ['tr-fmethod','tr-fkind','tr-fsrc']) document.getElementById(id).value='';
  document.getElementById('tr-search').value=''; renderTreasury();
}
let tpayDir='in';
function openTreasuryPayment(dir){
  tpayDir=dir;
  document.getElementById('tpay-title').textContent = dir==='in'?'تحصيل من عميل':'دفع لمورد';
  const kind = dir==='in'?'customer':'supplier';
  const list = customers.filter(c=>c.kind===kind);
  if(!list.length){toast(kind==='customer'?'ضيف عميل الأول':'ضيف مورد الأول',true);return;}
  document.getElementById('pm-customer').innerHTML=list.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  document.getElementById('pm-date').value=today();
  for(const id of ['pm-cash','pm-card','pm-transfer','pm-vcash','pm-cheque']) document.getElementById(id).value=0;
  for(const id of ['pm-cheque-due','pm-cheque-bank','pm-cheque-number','pm-notes']) document.getElementById(id).value='';
  document.getElementById('pm-cheque-meta').style.display='none';
  document.getElementById('tpay-modal').style.display='flex';
}
async function saveTreasuryPayment(){
  const body={customer_id:+v('pm-customer'),date:v('pm-date')||today(),direction:tpayDir,
    cash:v('pm-cash')||0,card:v('pm-card')||0,transfer:v('pm-transfer')||0,vcash:v('pm-vcash')||0,
    cheque:v('pm-cheque')||0,notes:v('pm-notes'),
    cheque_due:v('pm-cheque-due'),cheque_bank:v('pm-cheque-bank'),cheque_number:v('pm-cheque-number')};
  if(!(+body.cash||+body.card||+body.transfer||+body.vcash||+body.cheque)){toast('اكتب مبلغ الدفعة',true);return;}
  if(+body.cheque&&!body.cheque_due){toast('اكتب تاريخ استحقاق الشيك',true);return;}
  const payAge=Math.round((Date.parse(today())-Date.parse(body.date))/864e5);
  if(payAge>30 && !confirm('تنبيه: تاريخ الدفعة قديم ('+body.date+' - من '+payAge+' يوم).\nتحفظ برضه؟')) return;
  try{
    await api('/api/payments',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('tpay-modal').style.display='none';
    toast('تم حفظ الدفعة');
    refreshCustomers(); loadTreasury();
  }catch(e){toast(e.message,true);}
}
async function saveTreasury(){
  const body={kind:document.getElementById('tr-kind').value,category:v('tr-cat'),
    amount:v('tr-amount')||0,date:v('tr-date')||today(),notes:v('tr-notes'),
    method:document.getElementById('tr-method').value};
  try{
    await api('/api/treasury',{method:'POST',body:JSON.stringify(body)});
    toast('تم حفظ القيد');
    document.getElementById('treasury-entry-overlay').style.display='none';
    for(const id of ['tr-cat','tr-amount','tr-notes']) document.getElementById(id).value='';
    loadTreasury();
  }catch(e){toast(e.message,true);}
}
async function delTreasury(id){
  if(!confirm('حذف القيد رقم '+id+'؟')) return;
  try{ await api('/api/treasury/'+id,{method:'DELETE'}); toast('تم الحذف'); loadTreasury(); }
  catch(e){ toast(e.message,true); }
}
const CHEQUE_STATUS={pending:'تحت التحصيل',cashed:'تم الصرف',bounced:'مرتجع'};
async function loadCheques(){
  const st=document.getElementById('ch-filter').value;
  const r=await api('/api/cheques'+(st?'?status='+st:''));
  const list=r.cheques||[];
  const soonD=new Date(Date.now()+2*864e5).toISOString().slice(0,10);
  list.forEach(c=>{ c.due_soon = ['pending','endorsed'].includes(c.status) && !c.overdue && c.due_date<=soonD; });
  const t=r.totals||{};
  document.getElementById('ch-totals').innerHTML=[
    ['في إيدنا (تحت التحصيل)',fmt(t.in_hand||0)],['مظهّر لموردين',fmt(t.endorsed||0)],
    ['تم الصرف',fmt(t.cashed||0)],['مرتجع',fmt(t.bounced||0)],
  ].map(([tt,x])=>`<div class="card"><div class="t">${tt}</div><div class="v num">${x}</div></div>`).join('');
  document.querySelector('#ch-table tbody').innerHTML=list.map(c=>{
    let statusHtml;
    if(c.status==='cashed') statusHtml='<span class="ok">تم الصرف</span>';
    else if(c.status==='bounced') statusHtml='<span class="bad">مرتجع</span>';
    else if(c.status==='endorsed') statusHtml='<span>مظهّر لمورد'+(c.endorsed_name?' ('+esc(c.endorsed_name)+')':'')+'</span>';
    else statusHtml='تحت التحصيل';
    let acts=`<button class="small ghost" onclick="chequeHistory(${c.id})">حركة الشيك</button>`;
    if(c.status==='pending'){
      acts=` <button class="small" onclick="setCheque(${c.id},'cashed')">تم الصرف</button> <button class="small danger" onclick="setCheque(${c.id},'bounced')">مرتجع</button>`+acts;
      if(c.party_kind==='customer') acts=`<button class="small" onclick="openEndorse(${c.id})">تظهير لمورد</button>`+acts;
    } else if(c.status==='endorsed'){
      acts=` <button class="small" onclick="setCheque(${c.id},'cashed')">اتصرف عند المورد</button> <button class="small danger" onclick="setCheque(${c.id},'bounced')">مرتجع</button>`+acts;
    } else {
      acts=` <button class="small ghost" onclick="setCheque(${c.id},'pending')">إرجاع للانتظار</button>`+acts;
    }
    return `<tr${c.overdue?' style="background:var(--bad-soft)"':c.due_soon?' style="background:var(--warn-soft)"':''}>
    <td class="num ${c.overdue?'bad':''}"><b>${esc(c.due_date)}</b>${c.overdue?' (متأخر)':c.due_soon?' (قريب)':''}</td>
    <td>${esc(c.customer_name)}${c.party_kind==='supplier'?' (مورد)':''}</td>
    <td class="num"><b>${fmt(c.amount)}</b></td><td>${esc(c.bank)}</td><td class="num">${esc(c.cheque_number)}</td>
    <td>${statusHtml}</td>
    <td>${esc(c.notes)}${c.status==='endorsed'&&c.endorsed_date?'<div class="muted">تظهير: '+esc(c.endorsed_date)+'</div>':''}</td>
    <td style="white-space:nowrap">${acts}</td></tr>`;
  }).join('');
  renderChequeAgenda(list);
}
function renderChequeAgenda(all){
  const el=document.getElementById('ch-upcoming');
  if(!el) return;
  const t=today();
  const in30=new Date(Date.now()+30*864e5).toISOString().slice(0,10);
  const soon=new Date(Date.now()+2*864e5).toISOString().slice(0,10);
  const up=all.filter(c=>['pending','endorsed'].includes(c.status)&&c.due_date<=in30);
  if(!up.length){ el.innerHTML='<span class="muted">مفيش شيكات مستحقة في خلال 30 يوم</span>'; return; }
  const groups={};
  up.forEach(c=>{ (groups[c.due_date]=groups[c.due_date]||[]).push(c); });
  el.innerHTML=Object.keys(groups).sort().map(d=>{
    const label=d<t?'متأخر!':d===t?'النهاردة':d<=soon?'خلال يومين':d;
    const cls=d<t?'bad':d<=soon?'':'muted';
    return '<div style="margin:6px 0"><b class="'+cls+' num">'+label+(label!==d?' ('+d+')':'')+'</b>'
      +groups[d].map(c=>'<div style="display:flex;justify-content:space-between;padding:2px 8px;border-bottom:1px solid var(--line);font-size:13px"><span>'+esc(c.customer_name)+' - '+esc(c.bank||'')+' '+esc(c.cheque_number||'')+'</span><span class="num"><b>'+fmt(c.amount)+'</b></span></div>').join('')+'</div>';
  }).join('');
}
let endorseChequeId=null, endorseCache={};
async function openEndorse(cid){
  const r=await api('/api/cheques');
  const c=(r.cheques||[]).find(x=>x.id===cid);
  if(!c){toast('الشيك مش موجود',true);return;}
  endorseChequeId=cid; endorseCache=c;
  const sups=await api('/api/customers?kind=supplier');
  if(!sups.length){toast('ضيف موردين الأول من شاشة العملاء',true);return;}
  document.getElementById('end-supplier').innerHTML=sups.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
  document.getElementById('end-date').value=today();
  document.getElementById('end-note').value='';
  document.getElementById('end-info').innerHTML='شيك '+(esc(c.cheque_number)||'بدون رقم')+' - '+esc(c.customer_name)+' - <b class="num">'+fmt(c.amount)+'</b> جنيه - استحقاق '+esc(c.due_date);
  document.getElementById('endorse-modal').style.display='flex';
}
async function saveEndorse(){
  try{
    await api('/api/cheques/'+endorseChequeId+'/endorse',{method:'POST',body:JSON.stringify({
      supplier_id:+v('end-supplier'),date:v('end-date'),notes:v('end-note')})});
    document.getElementById('endorse-modal').style.display='none';
    toast('تم التظهير للمورد');
    loadCheques();
  }catch(e){toast(e.message,true);}
}
async function chequeHistory(cid){
  const r=await api('/api/cheques');
  const c=(r.cheques||[]).find(x=>x.id===cid);
  const h=await api('/api/cheques/'+cid+'/history');
  document.getElementById('chh-info').innerHTML=c
    ?('شيك '+(esc(c.cheque_number)||'بدون رقم')+' - '+esc(c.customer_name)+' - <b class="num">'+fmt(c.amount)+'</b> جنيه'):'';
  document.getElementById('chh-list').innerHTML=h.length
    ? h.map(e=>`<div style="border-right:3px solid #0ea5e9;padding:4px 10px;margin-bottom:8px">
        <div><b>${esc(e.event)}</b> <span class="muted num" style="font-size:12px">${esc((e.ts||'').replace('T',' ').slice(0,16))}</span></div>
        ${e.detail?`<div class="muted" style="font-size:13px">${esc(e.detail)}</div>`:''}</div>`).join('')
    : '<span class="muted">مفيش حركة مسجلة للشيك ده.</span>';
  document.getElementById('chh-modal').style.display='flex';
}
async function setCheque(id,status){
  try{ await api('/api/cheques/'+id+'/status',{method:'POST',body:JSON.stringify({status})});
    toast('تم التحديث'); loadCheques(); }
  catch(e){ toast(e.message,true); }
}

document.addEventListener('input',e=>{
  if(e.target && e.target.id==='pm-cheque')
    document.getElementById('pm-cheque-meta').style.display = (+e.target.value>0)?'flex':'none';
});

/* ---------- 1.7.0: today panel ---------- */
async function loadToday(){
  try{
    const t=await api('/api/today');
    document.getElementById('today-date').textContent=t.date;
    let h='<div class="cards">'
      +'<div class="card"><div class="t">داخل الخزنة النهاردة</div><div class="v num ok">'+fmt(t.treasury_in)+'</div></div>'
      +'<div class="card"><div class="t">خارج من الخزنة النهاردة</div><div class="v num bad">'+fmt(t.treasury_out)+'</div></div>'
      +'<div class="card"><div class="t">شيكات تستحق النهاردة</div><div class="v num">'+t.cheques_due_today.length+'</div></div>'
      +'<div class="card"><div class="t">شيكات متأخرة</div><div class="v num '+(t.cheques_overdue_count?'bad':'')+'">'+t.cheques_overdue_count+' ('+fmt(t.cheques_overdue_sum)+')</div></div>'
      +'<div class="card"><div class="t">عملاء متأخرين</div><div class="v num '+(t.overdue_customers_count?'bad':'')+'">'+t.overdue_customers_count+'</div></div>'
      +'</div>';
    const crow=c=>'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--line);font-size:13px"><span>'+esc(c.customer_name)+' - '+(c.bank||'')+' '+(c.cheque_number||'')+'</span><span class="num"><b>'+fmt(c.amount)+'</b> ('+c.due_date+')</span></div>';
    if(t.cheques_due_today.length)
      h+='<p style="color:#b45309;font-weight:600;margin:8px 0 2px">شيكات لازم تتحصل النهاردة:</p>'+t.cheques_due_today.map(crow).join('');
    if(t.cheques_due_soon.length)
      h+='<p style="color:#92400e;margin:8px 0 2px">شيكات بتستحق خلال '+t.cheque_alert_days+' يوم:</p>'+t.cheques_due_soon.map(crow).join('');
    if(t.cheques_overdue_count)
      h+='<p class="bad" style="margin:8px 0 2px">شيكات عدّت تاريخ استحقاقها وماتصرفتش:</p>'+t.cheques_week.filter(c=>c.overdue).map(crow).join('');
    if(t.overdue_customers.length)
      h+='<p class="bad" style="margin:8px 0 2px">عملاء متأخرين في السداد:</p>'+t.overdue_customers.map(c=>'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--line);font-size:13px"><span>'+esc(c.name)+'</span><span class="num"><b>'+fmt(c.balance)+'</b> - متأخر '+c.days+' يوم</span></div>').join('');
    if(!t.cheques_due_today.length && !t.cheques_due_soon.length && !t.cheques_overdue_count && !t.overdue_customers.length)
      h+='<p class="ok" style="margin:8px 0">مفيش مستحقات النهاردة ولا شيكات قريبة ولا عملاء متأخرين</p>';
    const wk=t.cheques_week.filter(c=>!c.overdue&&!c.due_today&&!c.due_soon);
    if(wk.length) h+='<details style="margin-top:6px"><summary class="muted" style="cursor:pointer">شيكات الأسبوع الجاي ('+wk.length+')</summary>'+wk.map(crow).join('')+'</details>';
    document.getElementById('today-body').innerHTML=h;
    document.getElementById('today-body').classList.remove('muted');
  }catch(e){ document.getElementById('today-body').textContent=e.message; }
}

/* ---------- quotations ---------- */
function qAddItemRow(it){
  it=it||{};
  const tr=document.createElement('tr');
  tr.innerHTML='<td><input class="it-name" list="se-items" value="'+esc(it.item_name||'')+'"></td>'
    +'<td><input class="it-color" value="'+esc(it.color||'')+'"></td>'
    +'<td><input class="it-pairs num" type="number" min="0" step="1" value="'+(it.pairs??'')+'" oninput="qCalc()"></td>'
    +'<td><input class="it-price num" type="number" min="0" step="0.01" value="'+(it.price??'')+'" oninput="qCalc()"></td>'
    +'<td class="it-total num">0</td>'
    +'<td><button class="small danger" onclick="this.closest(\"tr\").remove();qCalc()">×</button></td>';
  document.getElementById('q-items').appendChild(tr);
  qCalc();
}
function qReadItems(){
  const items=[]; let total=0, pairs=0;
  document.querySelectorAll('#q-items tr').forEach(tr=>{
    const name=tr.querySelector('.it-name').value.trim();
    const p=parseFloat(tr.querySelector('.it-pairs').value)||0;
    const pr=parseFloat(tr.querySelector('.it-price').value)||0;
    const t=p*pr; tr.querySelector('.it-total').textContent=fmt(t);
    if(name){ items.push({item_name:name,color:tr.querySelector('.it-color').value.trim(),pairs:p,price:pr}); total+=t; pairs+=p; }
  });
  return {items,total,pairs};
}
function qCalc(){ const r=qReadItems(); document.getElementById('q-total').textContent=fmt(r.total); document.getElementById('q-pairs').textContent=fmt(r.pairs); }
async function openQuoteModal(){
  await refreshCustomers();
  const sel=document.getElementById('q-customer');
  sel.innerHTML='<option value="">اختر العميل</option>'+customers.filter(c=>c.kind!=='supplier').map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
  document.getElementById('q-date').value=today();
  document.getElementById('q-notes').value='';
  document.getElementById('q-items').innerHTML='';
  qAddItemRow();
  openModal('quote-modal');
}
function qAutoPrice(name){
  const it=(itemsMaster||[]).find(x=>x.name===name);
  const c=customers.find(x=>x.id==v('q-customer'));
  if(!it) return 0;
  let p=(it.retail_price||0)/(wholesaleDivisor||1.22);
  if(c&&c.discount_pct) p=p*(1-c.discount_pct/100);
  return Math.round(p*100)/100;
}
function qSmartRow(){
  document.getElementById('qs-name').value='';
  document.getElementById('qs-price').value='';
  qsRenderChips();
  openModal('qsmart-modal');
}
function qsRenderChips(){NixtColorPicker.quantityChips('qs-colors',null,true)}

function qPriceLookup(){ const n=v('qs-name').trim(); if(!n) return; const p=qAutoPrice(n); if(p) document.getElementById('qs-price').value=p; }
function qSmartApply(){
  const name=v('qs-name').trim(); if(!name){toast('اكتب الموديل الأول',true);return;}
  const price=parseFloat(v('qs-price'))||0;
  let added=0;
  document.querySelectorAll('#qs-colors .se-chip').forEach(ch=>{
    if(ch.querySelector('.se-chip-on').checked){
      const qty=parseFloat(ch.querySelector('.se-chip-qty').value)||0;
      if(qty>0){ qAddItemRow({item_name:name,color:ch.dataset.color,pairs:qty,price:price}); added++; }
    }
  });
  if(!added){toast('علّم على لون واحد على الأقل واكتب العدد',true);return;}
  closeModal('qsmart-modal');
  qCalc();
}
async function saveQuote(){
  const cid=v('q-customer'); if(!cid){toast('اختر العميل أولاً',true);return;}
  const {items}=qReadItems(); if(!items.length){toast('أضف صنفاً واحداً على الأقل',true);return;}
  try{
    const r=await api('/api/quotations',{method:'POST',body:JSON.stringify({customer_id:+cid,date:v('q-date')||today(),notes:v('q-notes'),items})});
    toast('اتحفظ عرض السعر '+r.number);
    closeModal('quote-modal');
    loadQuotes();
  }catch(e){toast(e.message,true);}
}
const Q_STATUS={open:'مفتوح',converted:'اتحوّل لفاتورة',canceled:'ملغي'};
async function loadQuotes(){
  try{
    const list=await api('/api/quotations');
    document.querySelector('#q-table tbody').innerHTML=list.map(q=>'<tr>'
      +'<td class="num">'+esc(q.number||('#'+q.id))+'</td><td class="num">'+q.date+'</td><td>'+esc(q.customer_name)+'</td>'
      +'<td class="num">'+fmt(q.pairs)+'</td><td class="num"><b>'+fmt(q.total)+'</b></td>'
      +'<td>'+(q.status==='open'?'<span class="badge" style="background:var(--ok-soft)">مفتوح</span>':q.status==='converted'?'<span class="badge">اتحوّل لفاتورة</span>':'<span class="badge" style="background:var(--bad-soft)">ملغي</span>')+'</td>'
      +'<td style="white-space:nowrap">'
      +'<button class="small ghost" onclick="printQuote('+q.id+')">طباعة</button> '
      +(q.status==='open'?'<button class="small" onclick="convertQuote('+q.id+')">تحويل لفاتورة</button> <button class="small danger" onclick="cancelQuote('+q.id+')">إلغاء</button>':'')
      +'</td></tr>').join('')
      ||'<tr><td colspan="7" class="muted">مفيش عروض أسعار لسه</td></tr>';
  }catch(e){toast(e.message,true);}
}
async function printQuote(id){
  try{
    const q=await api('/api/quotations/'+id);
    const rows=q.items.map((it,i)=>'<tr><td class="num">'+(i+1)+'</td><td>'+esc(it.item_name)+'</td><td>'+esc(it.color||'')+'</td><td class="num">'+fmt(it.pairs)+'</td><td class="num">'+fmt(it.price)+'</td><td class="num">'+fmt(it.pairs*it.price)+'</td></tr>').join('');
    showPrint('<div class="doc">'
      +companyHeader(q.company||{}, 'عرض سعر رقم '+(q.number||('#'+q.id)))
      +'<div class="sub">التاريخ: <span class="num">'+q.date+'</span></div>'
      +'<div class="fields"><div><b>العميل:</b> '+esc(q.customer_name)+(q.customer_phone?' - تليفون: <span class="num">'+esc(q.customer_phone)+'</span>':'')+'</div></div>'
      +'<table><thead><tr><th>م</th><th>الصنف</th><th>اللون</th><th>أزواج</th><th>سعر الزوج</th><th>الإجمالي</th></tr></thead><tbody>'+rows+'</tbody></table>'
      +'<table style="width:50%;margin-right:auto"><tr><td><b>الإجمالي</b></td><td class="num"><b>'+fmt(q.total)+'</b></td></tr></table>'
      +(q.notes?'<p>'+esc(q.notes)+'</p>':'')
      +'<div class="sig"><span>توقيع العميل: ................</span><span>توقيع البائع: ................</span></div>'
      +'</div>');
  }catch(e){toast(e.message,true);}
}
async function convertQuote(id){
  if(!confirm('تحويل عرض السعر ده لفاتورة بيع بتاريخ النهاردة؟')) return;
  try{
    const r=await api('/api/quotations/'+id+'/convert',{method:'POST',body:'{}'});
    toast('اتعملت الفاتورة '+(r.invoice_number||'')+' من عرض السعر');
    loadQuotes();
  }catch(e){toast(e.message,true);}
}
async function cancelQuote(id){
  if(!confirm('إلغاء عرض السعر ده؟')) return;
  try{ await api('/api/quotations/'+id+'/cancel',{method:'POST',body:'{}'}); loadQuotes(); }
  catch(e){toast(e.message,true);}
}

/* ---------- model profit ---------- */
async function loadModelProfit(){
  try{
    const q=[]; if(v('mp-from')) q.push('from='+v('mp-from')); if(v('mp-to')) q.push('to='+v('mp-to'));
    const r=await api('/api/model-profit'+(q.length?'?'+q.join('&'):''));
    document.getElementById('mp-summary').innerHTML='<b>إجمالي الإيراد: <span class="num">'+fmt(r.total_revenue)+'</span> - إجمالي الربح: <span class="num">'+fmt(r.total_profit)+'</span></b>';
    document.querySelector('#mp-table tbody').innerHTML=r.items.map(x=>'<tr>'
      +'<td>'+esc(x.item_name)+'</td><td class="num">'+fmt(x.pairs)+'</td><td class="num">'+fmt(x.revenue)+'</td>'
      +'<td class="num">'+(x.has_cost?fmt(x.unit_cost):'<span class="bad">؟</span>')+'</td>'
      +'<td class="num '+(x.profit<0?'bad':'ok')+'"><b>'+fmt(x.profit)+'</b></td><td class="num">'+x.margin+'%</td></tr>').join('')
      ||'<tr><td colspan="6" class="muted">مفيش مبيعات في الفترة دي</td></tr>';
  }catch(e){toast(e.message,true);}
}

/* ---------- backups & restore ---------- */
async function loadBackups(){
  try{
    const r=await api('/api/backups');
    document.getElementById('bk-list').innerHTML = r.backups.length
      ? '<table style="width:100%"><thead><tr><th>النسخة</th><th>التاريخ</th><th>الحجم</th><th></th></tr></thead><tbody>'
        +r.backups.map(b=>'<tr><td class="num" style="font-size:12px">'+esc(b.file)+'</td><td class="num">'+b.mtime+'</td><td class="num">'+fmt(Math.round(b.size/1024))+' KB</td>'
        +'<td><button class="small danger" data-f="'+esc(b.file)+'" onclick="restoreBackup(this.dataset.f)">استعادة</button></td></tr>').join('')+'</tbody></table>'
        +'<p class="muted" style="font-size:12px">الفولدر: '+esc(r.dir)+'</p>'
      : '<p class="muted">مفيش نسخ احتياطية لسه</p>';
  }catch(e){toast(e.message,true);}
}
async function restoreBackup(file){
  if(!confirm('استعادة النسخة '+file+'؟\nكل البيانات الحالية هتتبدل ببيانات النسخة دي.\n(البرنامج هياخد نسخة أمان من الوضع الحالي قبل الاستبدال)')) return;
  try{
    const r=await api('/api/restore',{method:'POST',body:JSON.stringify({file})});
    alert('تمت الاستعادة من '+r.restored+'.\nنسخة أمان من الوضع السابق: '+r.safety_backup+'\nالبرنامج هيتحدث دلوقتي.');
    location.reload();
  }catch(e){toast(e.message,true);}
}

/* ---------- statement PDF + WhatsApp ---------- */
function statementPdfWhatsApp(){
  if(!lastStatement){toast('اعرض كشف الحساب الأول',true);return;}
  const c=customers.find(x=>x.id==v('s-customer'));
  const phone=(c&&c.phone||'').replace(/[^0-9]/g,'');
  printStatement(false);
  if(phone){
    const txt='كشف حسابكم من NIXT FOR LEATHER - المتبقي: '+fmt(lastStatement.summary.remaining)+' جنيه. هنبعت الـ PDF في الرسالة الجاية.';
    setTimeout(()=>window.open('https://wa.me/'+phone+'?text='+encodeURIComponent(txt),'_blank'),400);
  }else{
    toast('الطرف ده مالوش رقم تليفون مسجل - اطبع الـ PDF وابعته يدويًا',true);
  }
}

/* ---------- payment voucher ---------- */
async function printVoucher(id){
  try{
    const p=await api('/api/payments/'+id);
    const s=await api('/api/settings');
    const isSup = p.party_kind==='supplier';
    const methods=[['كاش',p.cash],['كارت',p.card],['تحويل بنكي',p.transfer],['فودافون كاش',p.vcash],['شيك',p.cheque]]
      .filter(m=>+m[1]>0).map(m=>'<tr><td>'+m[0]+'</td><td class="num">'+fmt(m[1])+'</td></tr>').join('');
    let chequeLine='';
    if(+p.cheque>0 && (p.cheque_bank||p.cheque_number||p.cheque_due))
      chequeLine='<p style="font-size:13px">الشيك: '+(p.cheque_bank||'')+' رقم '+(p.cheque_number||'-')+' - استحقاق '+(p.cheque_due||'-')+'</p>';
    showPrint(`<div class="doc">
      ${companyHeader(s, isSup?'سند صرف':'سند قبض')}
      <div class="sub">رقم السند: <span class="num">${p.id}</span> - التاريخ: <span class="num">${p.date}</span></div>
      <div class="fields"><div><b>${isSup?'صرفنا إلى':'استلمنا من'}:</b> ${esc(p.customer_name)}</div>
      <div><b>المبلغ:</b> <span class="num">${fmt(p.total)}</span> جنيه</div></div>
      ${p.notes?'<p>البيان: '+esc(p.notes)+'</p>':''}
      <table style="width:55%"><thead><tr><th>طريقة الدفع</th><th>المبلغ</th></tr></thead><tbody>${methods}</tbody></table>
      ${chequeLine}
      <div class="sig"><span>${isSup?'توقيع المستلم':'توقيع أمين الخزنة'}: ................</span><span>الاعتماد: ................</span></div>
    </div>`);
  }catch(e){toast(e.message,true);}
}

/* ---------- collections & reports ---------- */
async function loadCollections(){
  try{
    const list=await api('/api/collections');
    document.querySelector('#col-table tbody').innerHTML=list.map(c=>`<tr>
      <td>${esc(c.name)}</td><td class="num">${esc(c.phone||'-')}</td>
      <td class="num bad"><b>${fmt(c.balance)}</b></td>
      <td class="num">${esc(c.oldest_date||'-')}</td><td class="num">${c.days||0}</td>
      <td class="num">${esc(c.last_payment||'-')}</td>
      <td style="white-space:nowrap"><button class="small" onclick="openStatement(${c.id})">كشف</button>
      <button class="small ghost" onclick="shareCollection(${c.id},this.dataset.n)" data-n="${esc(c.name)}"><svg class="ic"><use href="#ic-phone"/></svg> تذكير واتساب</button></td></tr>`).join('')
      ||'<tr><td colspan="7" class="muted">مفيش مديونيات على العملاء</td></tr>';
    window._collections=list;
  }catch(e){toast(e.message,true);}
}
function shareCollection(id){
  const c=(window._collections||[]).find(x=>x.id===id); if(!c) return;
  const phone=(c.phone||'').replace(/[^0-9]/g,'');
  const txt='تحياتنا من NIXT FOR LEATHER 🌹\nتذكير ودّي بالمديونية: المتبقي على حسابكم '+fmt(c.balance)+' جنيه.'
    +(c.days>0?' أقدم مستحق من '+c.days+' يوم.':'')
    +'\nلو سمحت رتّب التحصيل في أقرب وقت، وشكرًا لتعاملكم معانا.';
  window.open('https://wa.me/'+phone+'?text='+encodeURIComponent(txt),'_blank');
}
async function loadTopItems(){
  try{
    const q=[]; if(v('tp-from')) q.push('from='+v('tp-from')); if(v('tp-to')) q.push('to='+v('tp-to'));
    const r=await api('/api/top-items'+(q.length?'?'+q.join('&'):''));
    document.querySelector('#tp-items tbody').innerHTML=r.items.map((x,i)=>`<tr>
      <td>${i<3?['<b>1</b>','<b>2</b>','<b>3</b>'][i]:''} ${esc(x.item_name)}</td><td class="num">${fmt(x.pairs)}</td><td class="num">${fmt(x.total)}</td></tr>`).join('')
      ||'<tr><td colspan="3" class="muted">مفيش مبيعات في الفترة دي</td></tr>';
    document.querySelector('#tp-colors tbody').innerHTML=r.colors.map((x,i)=>`<tr>
      <td><span class="dot" style="display:inline-block;width:10px;height:10px;border-radius:50%;border:1px solid rgba(0,0,0,.15);background:${COLOR_DOTS[x.color]||'#a8a29e'}"></span> ${esc(x.color)}</td><td class="num">${fmt(x.pairs)}</td><td class="num">${fmt(x.total)}</td></tr>`).join('')
      ||'<tr><td colspan="3" class="muted">مفيش مبيعات في الفترة دي</td></tr>';
  }catch(e){toast(e.message,true);}
}
let lastMonthly=null;
async function loadMonthlyReport(){
  const m=v('mc-month'); if(!m){toast('اختر الشهر الأول',true);return;}
  try{
    const r=await api('/api/monthly-report?month='+m);
    lastMonthly=r;
    document.getElementById('mc-close-btn').classList.toggle('hidden',!!r.closed);
    document.getElementById('mc-open-btn').classList.toggle('hidden',!r.closed);
    document.getElementById('mc-status').innerHTML = r.closed
      ? '<span class="badge" style="background:#fee2e2;border-color:#fca5a5"><svg class="ic"><use href="#ic-lock"/></svg> الشهر ده متقفل - التسجيل فيه متوقف</span>'
      : '<span class="badge" style="background:#dcfce7;border-color:#86efac">الشهر مفتوح</span>';
    const exRows=Object.entries(r.expenses||{}).map(([k,vv])=>`<tr><td>${esc(k)}</td><td class="num">${fmt(vv)}</td></tr>`).join('');
    document.getElementById('mc-report').innerHTML=`
      <div class="cards">
        <div class="card"><div class="t">المبيعات (صافي المرتجع)</div><div class="v num">${fmt(r.net_sales)}</div></div>
        <div class="card"><div class="t">المشتريات (صافي المرتجع)</div><div class="v num">${fmt(r.net_purchases)}</div></div>
        <div class="card"><div class="t">المحصّل من العملاء</div><div class="v num ok">${fmt(r.collected)}</div></div>
        <div class="card"><div class="t">المدفوع للموردين</div><div class="v num">${fmt(r.paid_suppliers)}</div></div>
        <div class="card"><div class="t">المصروفات</div><div class="v num bad">${fmt(r.expenses_total)}</div></div>
        <div class="card" style="background:var(--accent-soft)"><div class="t">الربح التقريبي</div><div class="v num">${fmt(r.gross)}</div></div>
      </div>
      <p class="muted">عدد الفواتير في الشهر: <span class="num">${r.invoice_count}</span></p>
      ${exRows?'<h3 style="font-size:14px;margin:8px 0">المصروفات حسب البند</h3><table style="width:50%"><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>'+exRows+'</tbody></table>':''}`;
  }catch(e){toast(e.message,true);}
}
async function closeMonth(){
  const m=v('mc-month'); if(!m){toast('اختر الشهر الأول',true);return;}
  if(!confirm('قفل شهر '+m+'؟ محدش هيقدر يسجل فواتير أو دفعات بتاريخ جوه الشهر ده غير لما تفتحه تاني.')) return;
  try{ await api('/api/monthly-close',{method:'POST',body:JSON.stringify({month:m})}); toast('اتقفل الشهر'); loadMonthlyReport(); }
  catch(e){toast(e.message,true);}
}
async function reopenMonth(){
  const m=v('mc-month'); if(!m) return;
  try{ await api('/api/monthly-open',{method:'POST',body:JSON.stringify({month:m})}); toast('اتفتح الشهر'); loadMonthlyReport(); }
  catch(e){toast(e.message,true);}
}
async function printMonthlyReport(){
  if(!lastMonthly){toast('اعرض التقرير الأول',true);return;}
  const r=lastMonthly;
  const s=await api('/api/settings');
  const exRows=Object.entries(r.expenses||{}).map(([k,vv])=>`<tr><td>${esc(k)}</td><td class="num">${fmt(vv)}</td></tr>`).join('');
  showPrint(`<div class="doc">
    ${companyHeader(s,'تقرير تقفيل شهر '+r.month)}
    <div class="sub">${r.closed?'الشهر متقفل <svg class="ic"><use href="#ic-lock"/></svg>':'الشهر مفتوح'} - عدد الفواتير: <span class="num">${r.invoice_count}</span></div>
    <table><thead><tr><th>البند</th><th>المبلغ (جنيه)</th></tr></thead><tbody>
      <tr><td>المبيعات</td><td class="num">${fmt(r.sales)}</td></tr>
      <tr><td>مرتجعات البيع</td><td class="num">-${fmt(r.returns)}</td></tr>
      <tr><td><b>صافي المبيعات</b></td><td class="num"><b>${fmt(r.net_sales)}</b></td></tr>
      <tr><td>المشتريات</td><td class="num">${fmt(r.purchases)}</td></tr>
      <tr><td>مرتجعات الشراء</td><td class="num">-${fmt(r.purchase_returns)}</td></tr>
      <tr><td><b>صافي المشتريات</b></td><td class="num"><b>${fmt(r.net_purchases)}</b></td></tr>
      <tr><td>المحصّل من العملاء</td><td class="num">${fmt(r.collected)}</td></tr>
      <tr><td>المدفوع للموردين</td><td class="num">${fmt(r.paid_suppliers)}</td></tr>
      <tr><td>إجمالي المصروفات</td><td class="num">${fmt(r.expenses_total)}</td></tr>
      <tr><td><b>الربح التقريبي</b></td><td class="num"><b>${fmt(r.gross)}</b></td></tr>
    </tbody></table>
    ${exRows?'<h3 style="font-size:14px">المصروفات حسب البند</h3><table style="width:60%"><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>'+exRows+'</tbody></table>':''}
    <div class="sig"><span>توقيع المحاسب: ................</span><span>توقيع المدير: ................</span></div>
  </div>`);
}

/* ---------- price lists & profit & whatsapp ---------- */
let currentPrices=[];
async function fillPriceCustomers(){
  const sel=document.getElementById('pl-customer');
  if(!sel) return;
  const r=await api('/api/customers?kind=customer');
  const keep=sel.value;
  sel.innerHTML='<option value="">اختر عميل...</option>'+r.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  if(keep) sel.value=keep;
}
async function loadPrices(){
  const cid=v('pl-customer');
  currentPrices=[];
  document.querySelector('#pl-table tbody').innerHTML='';
  if(!cid) return;
  currentPrices=await api('/api/prices?customer_id='+cid);
  document.querySelector('#pl-table tbody').innerHTML=currentPrices.map(p=>`<tr>
    <td>${esc(p.item_name)}</td><td class="num"><b>${fmt(p.price)}</b></td>
    <td><button class="small danger" onclick="delPrice(${p.id})">حذف</button></td></tr>`).join('');
}
function openPriceModal(){
  const sel=document.getElementById('plm-customer');
  const custs=customers.filter(c=>(c.kind||'customer')==='customer');
  sel.innerHTML='<option value="">اختر عميل...</option>'+custs.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  sel.value=v('pl-customer');
  document.getElementById('pl-item').value=''; document.getElementById('pl-price').value='';
  openModal('price-modal');
}
async function savePrice(){
  const cid=v('plm-customer');
  if(!cid){toast('اختر العميل الأول',true);return;}
  try{
    await api('/api/prices',{method:'POST',body:JSON.stringify({customer_id:+cid,item_name:v('pl-item'),price:v('pl-price')||0})});
    toast('تم حفظ السعر');
    document.getElementById('pl-item').value=''; document.getElementById('pl-price').value='';
    closeModal('price-modal');
    document.getElementById('pl-customer').value=cid;
    loadPrices();
  }catch(e){toast(e.message,true);}
}
async function delPrice(id){
  await api('/api/prices/'+id,{method:'DELETE'}); toast('تم الحذف'); loadPrices();
}
let invoicePrices=[];
async function priceLookup(el){
  const name=el.value.trim();
  if(!name) return;
  const tr=el.closest('tr');
  const priceEl=tr.querySelector('.it-price');
  if(parseFloat(priceEl.value)) return;
  const hit=invoicePrices.find(p=>p.item_name===name);
  if(hit){
    priceEl.value=hit.price;
    toast('سعر خاص للعميل: '+fmt(hit.price));
    calcInvoice();
    return;
  }
  const cid=v('i-customer');
  if(cid && document.getElementById('i-kind').value==='sale'){
    try{
      const r=await api('/api/item-last-price?customer_id='+encodeURIComponent(cid)+'&item_name='+encodeURIComponent(name));
      if(r && r.price!=null && !parseFloat(priceEl.value)){
        priceEl.value=r.price;
        toast('آخر سعر بيع للصنف ده: '+fmt(r.price)+(r.date?' (يوم '+r.date+')':''));
        calcInvoice();
      }
    }catch(e){}
  }
}
async function loadProfit(){
  const m=document.getElementById('pr-month').value;
  const r=await api('/api/profit'+(m?'?month='+m:''));
  document.getElementById('pr-cards').innerHTML=[
    ['المبيعات',fmt(r.sales)],['المرتجعات',fmt(r.returns)],['صافي المبيعات',fmt(r.net_sales)],
    ['التحصيلات',fmt(r.collected)],['مدفوعات للموردين',fmt(r.paid_suppliers)],
    ['المصروفات العامة',fmt(r.expenses_total)],['الربح التقريبي',fmt(r.gross)],
  ].map(([t,x])=>`<div class="card"><div class="t">${t}</div><div class="v num">${x}</div></div>`).join('');
  const ex=Object.entries(r.expenses||{});
  document.getElementById('pr-expenses').innerHTML = ex.length
    ? '<table><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>'+
      ex.map(([k,vv])=>`<tr><td>${esc(k)}</td><td class="num">${fmt(vv)}</td></tr>`).join('')+'</tbody></table>'
    : '<span class="muted">لا توجد مصروفات في الفترة دي.</span>';
}
function shareStatementWhatsApp(){
  if(!lastStatement){toast('اختر الطرف أولاً',true);return;}
  const s=lastStatement;
  const isSup = s.customer && s.customer.kind==='supplier';
  const phone=(s.customer.phone||'').replace(/[^0-9]/g,'');
  const txt='كشف حساب - '+s.customer.name+' - '+(isSup?'مستحق له':'المتبقي عليه')+': '+fmt(s.summary.remaining)+' جنيه - نظام نيكست للحسابات';
  const url='https://wa.me/'+(phone||'')+'?text='+encodeURIComponent(txt);
  window.open(url,'_blank');
  toast('اتفتح واتساب بملخص الكشف - اطبع الكشف PDF وأرفقه في المحادثة');
}

/* ---------- master data ---------- */
function openEmpModal(){
  for(const id of ['de-name','de-role','de-phone','de-start','de-notes']) document.getElementById(id).value='';
  openModal('emp-modal');
}
async function addEmployee(){
  if(!v('de-name')){ toast('اكتب اسم الموظف الأول', true); return; }
  try{
    await api('/api/employees',{method:'POST',body:JSON.stringify({name:v('de-name'),role:v('de-role'),phone:v('de-phone'),start_date:v('de-start'),notes:v('de-notes')})});
    toast('تمت إضافة الموظف');
    for(const id of ['de-name','de-role','de-phone','de-start','de-notes']) document.getElementById(id).value='';
    closeModal('emp-modal');
    loadEmployees();
  }catch(e){ toast(e.message,true); }
}
async function loadEmployees(){
  const list = await api('/api/employees');
  document.getElementById('employees-body').innerHTML = list.map(e=>
    `<tr><td>${esc(e.name)}</td><td>${esc(e.role||'')}</td><td class="num">${esc(e.phone||'')}</td><td class="num">${esc(e.start_date||'')}</td><td>${esc(e.notes||'')}</td><td>${e.active?'شغال':'موقوف'}</td><td><button class="ghost" onclick="toggleEmployee(${e.id})">${e.active?'إيقاف':'تشغيل'}</button></td></tr>`).join('');
}
async function toggleEmployee(id){
  await api('/api/employees/'+id+'/toggle',{method:'POST',body:'{}'});
  loadEmployees();
}

/* ---------- boot ---------- */
(function(){
  let rsT=null;
  window.addEventListener('resize',()=>{
    clearTimeout(rsT);
    rsT=setTimeout(()=>{
      if(!currentUser) return;
      const mx = window.outerWidth>=screen.availWidth-8 && window.outerHeight>=screen.availHeight-8;
      api('/api/window-size',{method:'POST',body:JSON.stringify({w:window.outerWidth,h:window.outerHeight,max:mx?1:0})}).catch(()=>{});
    },1500);
  });
})();
async function loadShortcutStatus(){
  try{
    const s=await api('/api/shortcut-status');
    const el=document.getElementById('sc-status');
    if(el) el.textContent = !s.windows ? '' : (s.exists ? 'الاختصار موجود: '+s.path : 'مفيش اختصار لسه');
  }catch(e){}
}
async function createShortcut(){
  try{
    await api('/api/create-shortcut',{method:'POST',body:'{}'});
    toast('اتعمل اختصار Nixt على سطح المكتب');
    document.getElementById('sc-banner').style.display='none';
    loadShortcutStatus();
  }catch(e){toast(e.message,true);}
}
async function dismissShortcutBanner(){
  document.getElementById('sc-banner').style.display='none';
  try{await api('/api/settings',{method:'POST',body:JSON.stringify({shortcut_dismissed:'1'})});}catch(e){}
}
async function shortcutBannerMaybe(){
  try{
    const s=await api('/api/shortcut-status');
    if(!s.windows||s.exists) return;
    const st=await api('/api/settings');
    if(st.shortcut_dismissed==='1') return;
    document.getElementById('sc-banner').style.display='flex';
  }catch(e){}
}
function quickAddParty(){
  const kind = KIND_PARTY[document.getElementById('i-kind').value] || 'customer';
  openPartyModal(kind);
}
async function loadItemMonthly(){
  const name=document.getElementById('im-name').value.trim();
  if(!name){ toast('اكتب اسم الصنف', true); return; }
  if(!itemNames || !itemNames.length){ try{ itemNames=await api('/api/item-names'); }catch(e){ itemNames=[]; } }
  document.getElementById('im-names').innerHTML=(itemNames||[]).map(n=>`<option value="${esc(n)}">`).join('');
  const r=await api('/api/item-monthly?name='+encodeURIComponent(name));
  const box=document.getElementById('im-result');
  if(r.matched && r.matched.length>1 && r.item!==name){
    box.innerHTML='<p class="muted">اختار الصنف المقصود: '+r.matched.map(n=>'<a href="#" onclick="document.getElementById(\'im-name\').value=\''+esc(n)+'\';loadItemMonthly();return false">'+esc(n)+'</a>').join(' | ')+'</p>';
    return;
  }
  if(!r.rows || !r.rows.length){ box.innerHTML='<p class="muted">مفيش مبيعات مسجلة للصنف ده</p>'; return; }
  box.innerHTML='<h3 style="font-size:14px;margin:8px 0">'+esc(r.item)+'</h3><table style="width:100%"><thead><tr><th>الشهر</th><th>أزواج</th><th>صافي البيع بعد الخصم</th></tr></thead><tbody>'
    +r.rows.map(x=>'<tr><td class="num">'+esc(x.month)+'</td><td class="num">'+fmt(x.pairs)+'</td><td class="num">'+fmt(Math.round(x.total))+'</td></tr>').join('')+'</tbody></table>';
}
async function bootData(){
  document.getElementById('i-date').value=today();
  document.getElementById('p-date').value=today();
  await refreshCustomers();
  if(!document.getElementById('i-items').children.length) addItemRow();
  await loadColors();
  loadItems(); loadItemNames();
  loadDash();
  shortcutBannerMaybe();
  if(currentUser && currentUser.role==='warehouse') showTab('inventory');
}
setInterval(()=>{ fetch('/api/version').catch(()=>{}); }, 30000);
(async function(){
  try{
    await bootAuth();
  }catch(e){
    showLogin();
    document.getElementById('login-err').textContent='مش قادر أتصل بالبرنامج. اقفل البرنامج وشغّله تاني، ولو المشكلة مستمرة اعمل Restart للجهاز.';
    const b=document.createElement('button');
    b.textContent='إعادة المحاولة'; b.onclick=()=>location.reload();
    document.getElementById('login-err').appendChild(document.createElement('br'));
    document.getElementById('login-err').appendChild(b);
  }
  if(currentUser){
    const m = location.hash.match(/tab=([a-z]+)/);
    if(m && document.getElementById('tab-'+m[1])) showTab(m[1]);
    bootData();
  }
})();
