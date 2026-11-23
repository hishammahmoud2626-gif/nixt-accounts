"""Customer demand, shared production batches and quantity-preserving allocations.
No financial posting occurs here. Invoice links are saved in the invoice transaction.
"""
import sqlite3, os, math
from datetime import datetime

SCHEMA = '''
CREATE TABLE IF NOT EXISTS production_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE,
 customer_id INTEGER NOT NULL REFERENCES customers(id), date TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '', cancelled INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS production_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES production_orders(id),
 item_name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '', size TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity>0), notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS production_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, date TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS production_allocations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES production_batches(id),
 line_id INTEGER NOT NULL REFERENCES production_lines(id), quantity INTEGER NOT NULL CHECK(quantity>0),
 created_at TEXT NOT NULL, UNIQUE(batch_id,line_id));
CREATE TABLE IF NOT EXISTS production_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, allocation_id INTEGER NOT NULL REFERENCES production_allocations(id),
 quantity INTEGER NOT NULL CHECK(quantity<>0), date TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 username TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS production_deliveries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, line_id INTEGER NOT NULL REFERENCES production_lines(id),
 quantity INTEGER NOT NULL CHECK(quantity<>0), date TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 username TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS production_invoice_links (
 id INTEGER PRIMARY KEY AUTOINCREMENT, line_id INTEGER NOT NULL REFERENCES production_lines(id),
 invoice_id INTEGER NOT NULL REFERENCES invoices(id), quantity INTEGER NOT NULL CHECK(quantity>0),
 created_at TEXT NOT NULL, UNIQUE(line_id,invoice_id));
CREATE TABLE IF NOT EXISTS production_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER REFERENCES production_orders(id),
 batch_id INTEGER REFERENCES production_batches(id), action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
 username TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
'''
TABLES=['production_orders','production_lines','production_batches','production_allocations','production_events','production_deliveries','production_invoice_links','production_history']
FK={'production_orders':{'customer_id':'customers'},'production_lines':{'order_id':'production_orders'},'production_allocations':{'batch_id':'production_batches','line_id':'production_lines'},'production_events':{'allocation_id':'production_allocations'},'production_deliveries':{'line_id':'production_lines'},'production_invoice_links':{'line_id':'production_lines','invoice_id':'invoices'},'production_history':{'order_id':'production_orders','batch_id':'production_batches'}}

def migrate(conn, path):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_orders'").fetchone():
        conn.commit()
        if os.path.exists(path) and os.path.getsize(path):
            dest=path+'.before-production-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.bak'
            tmp=dest+'.tmp'
            dst=sqlite3.connect(tmp)
            try:
                conn.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Pre-production backup failed')
            finally:dst.close()
            os.replace(tmp,dest)
    conn.executescript(SCHEMA)

class Workflow:
    def __init__(self, get_db, now):self.db=get_db;self.now=now
    def access(self,u,accounts=False):
        if not u or u.get('role') in ('view','mobile'):raise ValueError('الصلاحية دي مش متاحة للحساب')
        sections=(u.get('perms') or {}).get('sections')
        if u.get('role')!='admin' and (u.get('role')=='accountant' or (sections is not None and 'production' not in sections)):
            raise ValueError('محتاج صلاحية الإنتاج')
        if accounts and u.get('role')!='admin' and (u.get('role')=='warehouse' or (sections is not None and 'accounts' not in sections)):
            raise ValueError('محتاج صلاحية الحسابات لإنشاء الفاتورة')
    def n(self,v):
        if isinstance(v,bool):raise ValueError('الكمية لازم تكون عدد صحيح')
        try:x=float(v)
        except (ValueError,TypeError):raise ValueError('الكمية لازم تكون عدد صحيح')
        if not math.isfinite(x) or x!=int(x) or abs(x)>1000000:raise ValueError('الكمية لازم تكون عدد صحيح وفي الحدود المسموحة')
        return int(x)
    def date(self,v):
        v=v or self.now()[:10]
        try:datetime.strptime(v,'%Y-%m-%d')
        except (ValueError,TypeError):raise ValueError('التاريخ مش صحيح')
        return v
    def text(self,v,limit=500):return str(v or '').strip()[:limit]
    def hist(self,c,u,action,details='',oid=None,bid=None):
        c.execute('INSERT INTO production_history(order_id,batch_id,action,details,username,created_at) VALUES(?,?,?,?,?,?)',(oid,bid,action,details,u.get('username',''),self.now()))
    def lines(self,c):
        result=[]
        for row in c.execute('SELECT l.*,o.customer_id,o.number order_number,o.cancelled,c.name customer_name FROM production_lines l JOIN production_orders o ON o.id=l.order_id JOIN customers c ON c.id=o.customer_id ORDER BY l.id'):
            r=dict(row); lid=r['id']
            r['allocated']=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_allocations WHERE line_id=?',(lid,)).fetchone()[0]
            r['produced']=c.execute('SELECT COALESCE(SUM(e.quantity),0) FROM production_events e JOIN production_allocations a ON a.id=e.allocation_id WHERE a.line_id=?',(lid,)).fetchone()[0]
            r['delivered']=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_deliveries WHERE line_id=?',(lid,)).fetchone()[0]
            r['invoiced']=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_invoice_links WHERE line_id=?',(lid,)).fetchone()[0]
            r['ready']=r['produced']-r['delivered'];r['unallocated']=r['quantity']-r['allocated'];r['to_invoice']=r['delivered']-r['invoiced']
            result.append(r)
        return result
    def list(self,q,u):
        try:self.access(u)
        except ValueError as e:return 403,{'error':str(e)}
        c=self.db()
        try:
            lines=self.lines(c);orders=[]
            for row in c.execute('SELECT o.*,c.name customer_name FROM production_orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC'):
                r=dict(row);r['lines']=[x for x in lines if x['order_id']==r['id']]
                for k in ['quantity','allocated','produced','delivered','invoiced','ready','unallocated','to_invoice']:r[k]=sum(x[k] for x in r['lines'])
                r['status']='cancelled' if r['cancelled'] else ('complete' if r['delivered']==r['quantity'] and r['invoiced']==r['quantity'] else 'partial_delivery' if r['delivered'] else 'ready' if r['produced']==r['quantity'] else 'partial_ready' if r['produced'] else 'production' if r['allocated'] else 'new')
                orders.append(r)
            batches=[]
            for row in c.execute('SELECT * FROM production_batches ORDER BY id DESC'):
                b=dict(row);b['allocations']=[]
                for a0 in c.execute('SELECT * FROM production_allocations WHERE batch_id=?',(b['id'],)):
                    a=dict(a0);line=next(x for x in lines if x['id']==a['line_id']);a['line']=line
                    a['produced']=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_events WHERE allocation_id=?',(a['id'],)).fetchone()[0];b['allocations'].append(a)
                b['quantity']=sum(x['quantity'] for x in b['allocations']);b['produced']=sum(x['produced'] for x in b['allocations']);batches.append(b)
            return 200,{'orders':orders,'batches':batches,'lines':lines,'history':[dict(x) for x in c.execute('SELECT * FROM production_history ORDER BY id DESC LIMIT 100')],'invoice_links':[dict(x) for x in c.execute('SELECT p.*,i.number invoice_number FROM production_invoice_links p JOIN invoices i ON i.id=p.invoice_id ORDER BY p.id DESC')]}
        finally:c.close()
    def action(self,b,u):
        c=None
        try:
            self.access(u);c=self.db();c.execute('BEGIN IMMEDIATE');action=b.get('action');date=self.date(b.get('date'));note=self.text(b.get('notes'));now=self.now()
            if action=='order':
                cid=self.n(b.get('customer_id'));party=c.execute("SELECT id FROM customers WHERE id=? AND COALESCE(kind,'customer')='customer'",(cid,)).fetchone()
                if not party:raise ValueError('اختار عميل مسجل')
                ls=b.get('lines') or []
                if not ls or len(ls)>1000:raise ValueError('أضف كميات الطلبية')
                seq=c.execute('SELECT COALESCE(MAX(id),0)+1 FROM production_orders').fetchone()[0]
                oid=c.execute('INSERT INTO production_orders(number,customer_id,date,notes,created_at) VALUES(?,?,?,?,?)',('ORD-%05d'%seq,cid,date,note,now)).lastrowid
                grouped={}
                for x in ls:
                    name=self.text(x.get('item_name'),120);color=self.text(x.get('color'),80);size=self.text(x.get('size'),20);qty=self.n(x.get('quantity'));n=self.text(x.get('notes'))
                    if not name or not size or qty<=0:raise ValueError('الموديل والمقاس والكمية الموجبة مطلوبين')
                    key=(name,color,size,n);grouped[key]=grouped.get(key,0)+qty
                for (name,color,size,n),qty in grouped.items():c.execute('INSERT INTO production_lines(order_id,item_name,color,size,quantity,notes,created_at) VALUES(?,?,?,?,?,?,?)',(oid,name,color,size,qty,n,now))
                self.hist(c,u,'طلبية جديدة',oid=oid);result={'id':oid}
            elif action=='batch':
                ls=b.get('allocations') or []
                if not ls or len(ls)>1000:raise ValueError('حدد كميات التشغيل')
                current={x['id']:x for x in self.lines(c)};grouped={}
                for x in ls:
                    lid=self.n(x.get('line_id'));qty=self.n(x.get('quantity'))
                    if qty<=0:raise ValueError('كمية التشغيل لازم تكون موجبة')
                    grouped[lid]=grouped.get(lid,0)+qty
                for lid,qty in grouped.items():
                    r=current.get(lid)
                    if not r or r['cancelled'] or qty>r['unallocated']:raise ValueError('كمية التشغيل أكبر من المتبقي في الطلبية')
                seq=c.execute('SELECT COALESCE(MAX(id),0)+1 FROM production_batches').fetchone()[0]
                bid=c.execute('INSERT INTO production_batches(number,date,notes,created_at) VALUES(?,?,?,?)',('PR-%05d'%seq,date,note,now)).lastrowid
                for lid,qty in grouped.items():c.execute('INSERT INTO production_allocations(batch_id,line_id,quantity,created_at) VALUES(?,?,?,?)',(bid,lid,qty,now))
                self.hist(c,u,'تشغيلة جديدة',bid=bid);result={'id':bid}
            elif action in ('produce','deliver'):
                ls=b.get('entries') or []
                if not ls or len(ls)>1000:raise ValueError('حدد الكمية')
                grouped={}
                for x in ls:
                    key=self.n(x.get('allocation_id') if action=='produce' else x.get('line_id'));qty=self.n(x.get('quantity'))
                    if not qty:continue
                    grouped[key]=grouped.get(key,0)+qty
                if not grouped:raise ValueError('حدد كمية غير صفر')
                if any(v<0 for v in grouped.values()) and not note:raise ValueError('سبب التصحيح مطلوب')
                for key,qty in grouped.items():
                    current={x['id']:x for x in self.lines(c)}
                    if action=='produce':
                        a=c.execute('SELECT * FROM production_allocations WHERE id=?',(key,)).fetchone()
                        if not a:raise ValueError('بند التشغيل مش موجود')
                        r=current[a['line_id']];produced=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_events WHERE allocation_id=?',(key,)).fetchone()[0]
                        if r['cancelled'] or not 0<=produced+qty<=a['quantity'] or r['produced']+qty<r['delivered']:raise ValueError('كمية الإنتاج تتجاوز المخصص أو تقل عن المسلم')
                        c.execute('INSERT INTO production_events(allocation_id,quantity,date,reason,username,created_at) VALUES(?,?,?,?,?,?)',(key,qty,date,note,u.get('username',''),now));bid=a['batch_id']
                    else:
                        r=current.get(key)
                        if not r or r['cancelled'] or not r['invoiced']<=r['delivered']+qty<=r['produced']:raise ValueError('التسليم يتجاوز الجاهز أو يقل عن الكمية المفوترة')
                        c.execute('INSERT INTO production_deliveries(line_id,quantity,date,reason,username,created_at) VALUES(?,?,?,?,?,?)',(key,qty,date,note,u.get('username',''),now));bid=None
                    self.hist(c,u,'إنتاج' if action=='produce' else 'تسليم',str(qty)+' | '+note,oid=r['order_id'],bid=bid)
                result={'ok':True}
            elif action=='release':
                aid=self.n(b.get('allocation_id'));qty=self.n(b.get('quantity'))
                if qty<=0 or not note:raise ValueError('حدد كمية وسبب فك التخصيص')
                a=c.execute('SELECT * FROM production_allocations WHERE id=?',(aid,)).fetchone()
                if not a:raise ValueError('بند التشغيل مش موجود')
                made=c.execute('SELECT COALESCE(SUM(quantity),0) FROM production_events WHERE allocation_id=?',(aid,)).fetchone()[0]
                if qty>a['quantity']-made:raise ValueError('تقدر تفك الكمية اللي لسه ما اتنتجتش فقط')
                if qty==a['quantity']:
                    if c.execute('SELECT COUNT(*) FROM production_events WHERE allocation_id=?',(aid,)).fetchone()[0]:raise ValueError('البند عليه سجل إنتاج وتصحيح، احتفظ به للسجل')
                    c.execute('DELETE FROM production_allocations WHERE id=?',(aid,))
                else:c.execute('UPDATE production_allocations SET quantity=quantity-? WHERE id=?',(qty,aid))
                oid=c.execute('SELECT order_id FROM production_lines WHERE id=?',(a['line_id'],)).fetchone()[0]
                self.hist(c,u,'فك تخصيص',str(qty)+' | '+note,oid=oid,bid=a['batch_id']);result={'ok':True}
            elif action=='cancel':
                oid=self.n(b.get('order_id'));r=c.execute('SELECT * FROM production_orders WHERE id=?',(oid,)).fetchone()
                if not r:raise ValueError('الطلبية مش موجودة')
                ls=[x for x in self.lines(c) if x['order_id']==oid]
                if any(x['allocated'] or x['delivered'] or x['invoiced'] for x in ls):raise ValueError('الطلبية مرتبطة بتشغيل، لازم تصحيح مخصص قبل إلغائها')
                if not note:raise ValueError('سبب الإلغاء مطلوب')
                c.execute('UPDATE production_orders SET cancelled=1 WHERE id=?',(oid,));self.hist(c,u,'إلغاء طلبية',note,oid=oid);result={'ok':True}
            else:raise ValueError('عملية غير صحيحة')
            c.commit();return 200,result
        except (ValueError,TypeError,sqlite3.IntegrityError) as e:
            if c:c.rollback()
            return 400,{'error':str(e)}
        finally:
            if c:c.close()
    def validate_invoice(self,c,b,u):
        selected=b.get('production_selection')
        if not selected:return []
        self.access(u,True)
        if b.get('kind','sale')!='sale':raise ValueError('طلبيات الإنتاج تتحول لفاتورة بيع فقط')
        if not isinstance(selected,list) or len(selected)>1000:raise ValueError('اختيار الإنتاج غير صحيح')
        current={x['id']:x for x in self.lines(c)};expected={};seen=set();out=[]
        for x in selected:
            lid=self.n(x.get('line_id'));qty=self.n(x.get('quantity'));r=current.get(lid)
            if lid in seen or not r or r['cancelled'] or qty<=0 or qty>r['to_invoice'] or self.n(b.get('customer_id'))!=r['customer_id']:raise ValueError('الكمية أو العميل غير مطابق، أو الكمية اتفوترت بالفعل')
            seen.add(lid);key=(r['item_name'],r['color']);expected[key]=expected.get(key,0)+qty;out.append((lid,qty))
        actual={}
        for x in b.get('items') or []:
            if not self.text(x.get('item_name')):continue
            qty=self.n(x.get('pairs'));key=(self.text(x.get('item_name')),self.text(x.get('color')))
            if qty<=0:raise ValueError('كمية الفاتورة لازم تكون موجبة')
            actual[key]=actual.get(key,0)+qty
        if actual!=expected:raise ValueError('بنود الفاتورة لازم تطابق كميات الإنتاج المحددة، ارجع للإنتاج لتغيير الاختيار')
        return out
    def link_invoice(self,c,iid,selection,u):
        for lid,qty in selection:
            c.execute('INSERT INTO production_invoice_links(line_id,invoice_id,quantity,created_at) VALUES(?,?,?,?)',(lid,iid,qty,self.now()))
            oid=c.execute('SELECT order_id FROM production_lines WHERE id=?',(lid,)).fetchone()[0]
            self.hist(c,u,'فاتورة بيع',str(iid)+' | '+str(qty),oid=oid)
