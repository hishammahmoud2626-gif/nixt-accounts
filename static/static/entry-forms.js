/* Move existing forms without changing their fields or save logic. */
function floatingEntry(form,title,id){
 if(!form)return;const bg=document.createElement('div');bg.id=id;bg.className='modal-bg floating-entry';bg.style.display='none';const modal=document.createElement('div');modal.className='modal';const top=document.createElement('div');top.className='entry-top';const h=document.createElement('h3');h.textContent=title;const close=document.createElement('button');close.className='ghost';close.textContent='إغلاق ×';close.onclick=()=>{bg.style.display='none'};top.append(h,close);modal.append(top,form);bg.append(modal);document.body.append(bg);return bg;
}
document.addEventListener('DOMContentLoaded',()=>{
 const payments=document.querySelector('#tab-payments>.panel'),pay=floatingEntry(payments,'تسجيل دفعة','payment-entry-overlay');
 const payButton=document.createElement('button');payButton.textContent='+ تسجيل دفعة';payButton.onclick=async()=>{await refreshCustomers();pay.style.display='flex';enhanceRegisteredControls()};document.querySelector('#tab-payments>.page-actionbar').append(payButton);
 const manual=document.getElementById('tr-kind').closest('.panel'),treas=floatingEntry(manual,'قيد خزينة يدوي','treasury-entry-overlay');
 const trButton=document.createElement('button');trButton.textContent='+ قيد يدوي';trButton.onclick=()=>{treas.style.display='flex';enhanceRegisteredControls()};document.querySelector('#tab-treasury>.page-actionbar').append(trButton);
 const sizeField=document.getElementById('st-size').closest('.field');const mode=document.createElement('select');mode.id='st-size-mode';mode.innerHTML='<option value="specified">مقاس محدد</option><option value="unspecified">غير محدد، إجمالي لون</option>';sizeField.prepend(mode);mode.onchange=()=>{const input=document.getElementById('st-size');input.disabled=mode.value==='unspecified';if(input.disabled)input.value=''};
 const stockForm=document.getElementById('stock-form'),stock=floatingEntry(stockForm,'حركة مخزن','stock-entry-overlay');
 const originalStockOpen=stockOpen;stockOpen=function(kind){originalStockOpen(kind);document.getElementById('st-size-mode').value='specified';document.getElementById('st-size').disabled=false;stock.style.display='flex';enhanceRegisteredControls()};
 new MutationObserver(()=>{if(stockForm.classList.contains('hidden'))stock.style.display='none'}).observe(stockForm,{attributes:true,attributeFilter:['class']});
 // Form action proxies are local to each popup, never left on the record page.
 for(const f of [payments,manual,stockForm]){f.querySelectorAll('.top-action-source').forEach(b=>{b._topProxy?.remove();b.classList.remove('top-action-source');delete b.dataset.topAction});}
 enhanceRegisteredControls();
});
