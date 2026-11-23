/* Shared color selector. The real field remains the value source. */
const NIXT_BASE_COLORS=['أسود','بني','هافان'];
const NixtColorPicker={
 extras:[],
 available(){return [...new Set([...NIXT_BASE_COLORS,...colorsList,...this.extras])]},
 render(box,selected,onChange,multiple=false){
  box.replaceChildren();box.classList.add('color-picker');
  const chosen=Array.isArray(selected)?selected:[selected];
  for(const color of [...new Set([...NIXT_BASE_COLORS,...chosen])].filter(Boolean)){
   const button=document.createElement('button');button.type='button';button.dataset.noTopbar='1';button.className='color-tile '+(chosen.includes(color)?'active':'');button.setAttribute('aria-pressed',String(chosen.includes(color)));
   const dot=document.createElement('i');dot.style.background=COLOR_DOTS[color]||'#8390a3';button.append(dot,document.createTextNode(color));button.onclick=()=>onChange(multiple?(chosen.includes(color)?chosen.filter(x=>x!==color):[...chosen,color]):color);box.append(button);
  }
  const extra=document.createElement('button');extra.type='button';extra.dataset.noTopbar='1';extra.className='ghost';extra.textContent='+ لون';extra.onclick=()=>this.choose(color=>onChange(multiple?[...new Set([...chosen,color])]:color));box.append(extra);
 },
 choose(callback){
  let bg=document.getElementById('shared-color-dialog');if(!bg){bg=document.createElement('div');bg.id='shared-color-dialog';bg.className='modal-bg';bg.style.zIndex='70';document.body.append(bg)}
  bg.innerHTML='<div class="modal" data-color-dialog="1"><h3>اختار لون إضافي</h3><div class="modal-actionbar"><button id="shared-color-add">اختيار اللون</button><button class="ghost" onclick="closeModal(\'shared-color-dialog\')">إلغاء</button></div><input id="shared-color-value" list="shared-color-list" placeholder="ابحث عن لون مسجل أو اكتب لون جديد"><datalist id="shared-color-list"></datalist><p class="muted">اللون الجديد يفضل ضمن اختيارات الجلسة، ويتحفظ مع السجل عند حفظه.</p></div>';
  const list=bg.querySelector('datalist');for(const color of this.available()){const o=document.createElement('option');o.value=color;list.append(o)}
  bg.querySelector('#shared-color-add').onclick=()=>{const color=bg.querySelector('input').value.trim();if(!color||color.length>80||/[,،]/.test(color)){toast('اكتب اسم لون واحد صحيح',true);return}if(!this.extras.includes(color))this.extras.push(color);closeModal('shared-color-dialog');callback(color)};openModal('shared-color-dialog');bg.querySelector('input').focus();
 },
 quantityChips(boxId,totalHandler,reset=false){
  const box=document.getElementById(boxId),keep={};if(!reset)box.querySelectorAll('.se-chip').forEach(ch=>keep[ch.dataset.color]={on:ch.querySelector('.se-chip-on').checked,q:ch.querySelector('.se-chip-qty').value});
  const paint=()=>{box.replaceChildren();for(const color of [...new Set([...NIXT_BASE_COLORS,...Object.keys(keep)])]){const state=keep[color]||{};const label=document.createElement('label');label.className='se-chip '+(state.on?'on':'');label.dataset.color=color;const dot=document.createElement('span');dot.className='dot';dot.style.background=COLOR_DOTS[color]||'#8390a3';const on=document.createElement('input');on.type='checkbox';on.className='se-chip-on';on.checked=!!state.on;const qty=document.createElement('input');qty.type='number';qty.min='0';qty.step='1';qty.placeholder='0';qty.className='se-chip-qty q num';qty.value=state.q||'';on.onchange=()=>{label.classList.toggle('on',on.checked);keep[color]={on:on.checked,q:qty.value};totalHandler?.()};qty.oninput=()=>{keep[color]={on:on.checked,q:qty.value};totalHandler?.()};label.append(dot,on,document.createTextNode(color),qty);box.append(label)}const plus=document.createElement('button');plus.type='button';plus.dataset.noTopbar='1';plus.className='ghost';plus.textContent='+ لون';plus.onclick=()=>this.choose(color=>{keep[color]=keep[color]||{on:true,q:''};paint()});box.append(plus)};paint();
 },
 mount(input){
  if(input.dataset.smartColor){if(input._colorValue!==input.value)input._colorPaint?.();return;}input.dataset.smartColor='1';input.classList.add('color-value-source');const box=document.createElement('div');input.after(box);const paint=()=>{input._colorValue=input.value;this.render(box,input.value,color=>{input.value=color;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));paint()})};input._colorPaint=paint;paint();input.addEventListener('change',paint);
 },
 enhance(){document.querySelectorAll('input.it-color,input.po-color,#pm-color,#st-color').forEach(input=>this.mount(input))}
};
