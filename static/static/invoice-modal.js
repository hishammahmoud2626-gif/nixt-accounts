/* Existing invoice form, floating above its full-width movement record. */
(function(){
 const baseShowTab=showTab;
 const overlay=document.createElement('div');overlay.id='invoice-entry-overlay';overlay.className='modal-bg';overlay.style.display='none';
 const frame=document.createElement('div');frame.className='modal invoice-entry-modal';const head=document.createElement('div');head.className='invoice-modal-title';head.innerHTML='<h3 id="invoice-floating-title">فاتورة جديدة</h3><button class="ghost" type="button" onclick="closeInvoiceEntry()">إغلاق ×</button>';frame.append(head);
 const section=document.getElementById('tab-invoice');section.querySelectorAll('.top-action-source').forEach(b=>{b.classList.remove('top-action-source');delete b.dataset.topAction});section.querySelector('.page-actionbar')?.remove();frame.append(section);overlay.append(frame);document.body.append(overlay);
 window.closeInvoiceEntry=function(){overlay.style.display='none';section.classList.add('hidden');loadMovements(v('i-kind').includes('purchase')?'purchase':'sale')};
 showTab=function(t,kind){
  if(t==='invoice'){
   const supplier=v('i-kind').includes('purchase');baseShowTab(supplier?'purchases':'sales');section.classList.remove('hidden');overlay.style.display='flex';document.getElementById('invoice-floating-title').textContent=supplier?'فاتورة مشتريات':'فاتورة بيع';loadInvoices();if(typeof enhanceRegisteredControls==='function')enhanceRegisteredControls();return;
  }
  if(overlay.style.display!=='none'){overlay.style.display='none';section.classList.add('hidden')}
  return baseShowTab(t,kind);
 };
 const baseNew=newInvoiceFromSection;newInvoiceFromSection=function(kind){document.getElementById('i-kind').value=kind;baseNew(kind);document.getElementById('invoice-floating-title').textContent=kind.includes('purchase')?'فاتورة مشتريات':'فاتورة بيع'};
})();
