'use strict';
const flow=(kind,p)=>api('/api/workflow/'+kind,p);
const centsInput=(p,key)=>{p[key]=Math.round(Number(p[key])*100);return p};
function renderExtra(page){
 if(page==='plans'){
  $('#app').innerHTML=head('每次服務，都有依據。','方案價格與實際次數獨立管理；補掃不重複扣次。',btn('新增方案','new-plan'))+'<div class="panel">'+table(['客戶 / 方案','有效期間','核定方案價格','剩餘 / 已排 / 可排','條件',''],state.plans.map(p=>'<tr><td>'+esc(cname(p.customer_id))+'<small>'+esc(p.name)+'</small></td><td>'+esc(p.valid_from+' ～ '+p.valid_to)+'</td><td>'+money(p.price)+'</td><td>'+p.remaining+' / '+p.reserved+' / '+p.available+'</td><td>'+esc(p.terms)+'</td><td>'+btn('展延 / 調整','plan-adjust',p.id,'quiet')+'</td></tr>'))+'<p>建立方案不代表已收款。請另外建立方案帳單，預收款經核對後分攤。</p></div>';return true;
 }
 if(page==='receipts'){
  $('#app').innerHTML=head('款項先核對，再入帳。','聊天中的「已匯款」只是待核對聲明；不會自動標成已收。',btn('登記收款','new-receipt'))+'<div class="panel">'+table(['客戶 / 日期','狀態','收到金額','已分攤','已退','待分攤','憑證',''],state.receipts.map(r=>'<tr><td>'+esc(cname(r.customer_id))+'<small>'+esc(r.date)+'</small></td><td>'+badge(r.status)+'</td><td>'+money(r.amount)+'</td><td>'+money(r.allocated)+'</td><td>'+money(r.refunded)+'</td><td>'+money(r.available)+'</td><td>'+esc(r.reference)+'</td><td>'+(r.status==='pending'?btn('人工對帳','receipt-verify',r.id,'quiet'):btn('分攤帳單','allocate',r.id,'quiet')+btn('預收退款','receipt-refund',r.id,'quiet'))+'</td></tr>'))+'</div><div class="panel"><h2>帳單減免</h2><p>退還已列帳款項前，先記錄減免原因。這樣退款不會重新變成待催收欠款。</p>'+table(['客戶 / 帳期','核定減免','可退溢收',''],state.invoices.filter(i=>i.status==='issued').map(i=>'<tr><td>'+esc(cname(i.customer_id)+' / '+i.period)+'</td><td>'+money(i.credits)+'</td><td>'+money(i.refundable)+'</td><td>'+btn('新增減免','credit',i.id,'quiet')+btn('登記退款','payment',i.id,'quiet')+'</td></tr>'))+'</div>';return true;
 }
 if(page==='tasks'){
  const section=(title,list,kind)=>'<div class="panel"><h2>'+title+'</h2>'+table(['主旨','負責人','期限','狀態','處理內容',''],list.map(t=>'<tr><td>'+esc(t.title)+'<small>'+esc(t.detail)+'</small></td><td>'+esc(t.owner)+'</td><td>'+esc(t.due_date)+'</td><td>'+badge(t.status)+'</td><td>'+esc(t.resolution)+'</td><td>'+btn('更新',kind,t.id,'quiet')+'</td></tr>'))+'</div>';
  $('#app').innerHTML=head('每個例外，都有人接住。','內部待辦、客訴、補掃與續約追蹤；不發送 LINE。',btn('新增待辦','new-task')+btn('新增品質案件','new-case','','secondary'))+section('營運待辦',state.tasks,'edit-task')+section('品質與異常',state.service_cases,'edit-case');return true;
 }
 if(page==='chats'){
  $('#app').innerHTML=head('對話留存，決定可追溯。',state.chat_threads.length+' 份示範服務店 CSV。原始訊息只讀，分類與客戶關聯由人工確認。')+'<div class="panel"><div class="filters"><input id="chat-search" aria-label="搜尋對話內容" placeholder="關鍵字，例如：改期、已匯款"><select id="chat-thread" aria-label="選擇對話檔案"><option value="">全部對話</option>'+state.chat_threads.map(t=>'<option value="'+t.id+'">'+esc(t.source)+'</option>').join('')+'</select>'+btn('搜尋','chat-search')+btn('分類與關聯','chat-link','','secondary')+'</div><div id="chat-results"><p>選擇檔案或搜尋關鍵字。照片／影片僅有占位文字，不代表附件已下載。</p></div></div>';return true;
 }
 if(page==='calendar'){
  $('#app').innerHTML=head('這一週，安排清楚。','只顯示已確認排班；尚未核對的來源不會被當成正式預約。')+'<div class="panel"><div class="filters"><label>起始日期 <input id="week-date" type="date" value="'+state.today+'"></label></div><div id="week-results"></div></div>';
  const draw=()=>{const day=$('#week-date').value;const end=new Date(day+'T12:00:00+08:00');end.setDate(end.getDate()+7);const until=end.toISOString().slice(0,10);$('#week-results').innerHTML=table(['時間','服務案場','狀態','夥伴','金額',''],orderRows(state.orders.filter(o=>['confirmed','completed'].includes(o.status)&&o.date>=day&&o.date<until).sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start))))};$('#week-date').addEventListener('input',draw);draw();return true;
 }return false;
}
async function extraAction(name,id){
 if(name==='customer'){
  const c=find('customers',id)||{};dialog('客戶資料',(id?'<div class="full">'+btn('聯絡人／家人／請款窗口','contacts',id,'secondary')+'</div>':'')+field('name','客戶名称 *',c.name)+field('line_name','LINE顯示名稱',c.line_name)+field('phone','電話',c.phone)+field('line_account','官方帳號',c.line_account,'text',[['','待確認'],...state.line_accounts.map(a=>[a.id,a.name])])+field('status','狀態',c.status||'active','text',[['active','使用中'],['paused','暫停']])+field('billing_rule','結帳規則',c.billing_rule)+field('notes','備註',c.notes,'textarea',null,'',true),fd=>api('/api/save/customers',{...object(fd),...(id?{id,version:c.version}:{})}));return true;
 }
 if(name==='photos'){
  const o=find('orders',id),list=state.attachments.filter(p=>p.order_id===id);
  dialog('現場服務照片','<div class="full">'+list.map(p=>'<a href="/api/photo/'+encodeURIComponent(p.id)+'" target="_blank" rel="noopener">'+esc(p.service_date+' '+p.label+' '+p.name)+'</a><br>').join('')+'</div>'+field('label','用途','after','text',[['before','服務前'],['after','服務後'],['incident','異常證據']])+field('service_date','拍攝／服務日期',o.date||state.today,'date')+'<div class="field wide"><label for="photo-file">PNG／JPEG（限 5 MB）</label><input id="photo-file" type="file" accept="image/png,image/jpeg"></div>',async fd=>{const f=$('#photo-file').files[0];if(!f)throw new Error('請選擇照片');if(f.size>5*1024*1024)throw new Error('照片超過 5 MB');const content=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(f)});return api('/api/photo',{...object(fd),order_id:id,name:f.name,content})},'保存照片');return true;
 }
 if(name==='site'){
  const s=find('sites',id)||{};dialog('服務案場',field('name','案場名稱 *',s.name)+field('customer_id','所屬客戶 *',s.customer_id,'text',opts('customers'))+field('address','完整地址',s.address,'text',null,'',true)+field('navigation','導航定位／正確入口',s.navigation,'textarea',null,'可填定位連結，避免導航誤帶至後門。',true)+field('entry_notes','門禁／停車／鑰匙交接',s.entry_notes,'textarea',null,'只保留在受保護的後台。',true)+field('checklist','服務重點與工具清單',s.checklist,'textarea',null,'',true)+field('service_type','服務類型',s.service_type)+field('rate','參考時薪（元）',s.rate,'number')+field('pricing_note','客製計費方案',s.pricing_note,'textarea')+field('notes','其他備註',s.notes,'textarea'),fd=>api('/api/save/sites',{...object(fd),...(id?{id,version:s.version}:{})}));return true;
 }
 if(name==='contacts'){
  dialog('客戶聯絡窗口','<div class="full">'+table(['姓名 / 角色','電話','LINE別名',''],state.contacts.filter(c=>c.customer_id===id).map(c=>'<tr><td>'+esc(c.name+' / '+c.role)+'</td><td>'+esc(c.phone)+'</td><td>'+esc(c.alias)+'</td><td>'+btn('編輯','contact',c.id,'quiet')+'</td></tr>'))+btn('新增聯絡窗口','new-contact',id,'secondary')+'</div>',async()=>{},'關閉');return true;
 }
 if(name==='contact'||name==='new-contact'){
  const c=name==='contact'?find('contacts',id):{customer_id:id};dialog('聯絡窗口',field('name','姓名',c.name)+field('role','角色',c.role||'聯絡人')+field('phone','電話',c.phone)+field('alias','LINE別名',c.alias)+field('line_account','官方帳號',c.line_account,'text',[['','待確認'],...state.line_accounts.map(a=>[a.id,a.name])])+field('notes','關係／聯絡注意事項',c.notes,'textarea'),fd=>api('/api/save/contacts',{...object(fd),customer_id:c.customer_id,...(c.id?{id:c.id,version:c.version}:{})}));return true;
 }
 if(name==='new-plan'){
  dialog('新增客製方案',field('customer_id','客戶','','text',opts('customers'))+field('name','方案名稱')+field('valid_from','生效日',state.today,'date')+field('valid_to','到期日',state.today,'date')+field('price','方案金額（元）','','number')+field('units','可用次數',1,'number')+field('terms','已同意條件／人數／時數／跨場／退款規則','','textarea',null,'每次實際費用仍由人工核定。',true),fd=>flow('plan',centsInput(object(fd),'price')));return true;
 }
 if(name==='plan-adjust'){
  const p=find('plans',id);dialog('方案展延／次數調整',field('valid_to','新到期日',p.valid_to,'date')+field('units','增減次數（不變填 0）',0,'number')+field('reason','人工核准依據','','textarea'),fd=>flow('plan-adjust',{...object(fd),id}));return true;
 }
 if(name==='new-receipt'){
  const req=crypto.randomUUID();dialog('登記待對帳款項',field('customer_id','客戶','','text',opts('customers'))+field('amount','收到金額（元）','','number')+field('kind','類型','payment','text',[['payment','服務款'],['prepayment','預收 / 訂金']])+field('date','收款日期',state.today,'date')+field('method','方式','匯款')+field('reference','匯款末碼／來源憑證'),fd=>flow('receipt',{...centsInput(object(fd),'amount'),request_key:req}));return true;
 }
 if(name==='receipt-verify'){
  dialog('人工確認銀行／現金已實收',field('reference','對帳證據／核對人與日期','','textarea',null,'請實際核對銀行或現金，不能僅依對話聲明。',true),fd=>flow(name,{id,...object(fd)}));return true;
 }
 if(['allocate','receipt-refund','credit'].includes(name)){
  const req=crypto.randomUUID(),r=name==='credit'?find('invoices',id):find('receipts',id);
  let fields=name==='allocate'?field('invoice_id','對應已核定帳單','','text',[['','請選擇'],...state.invoices.filter(i=>i.customer_id===r.customer_id&&i.status==='issued'&&i.balance>0).map(i=>[i.id,i.period+' '+money(i.balance)])]):field('reason','原因','','textarea');
  if(name==='receipt-refund')fields+=field('reference','已退款的交易憑證');
  dialog({allocate:'分攤收款',credit:'建立帳单減免','receipt-refund':'登記預收餘額退款'}[name],fields+field('amount','金額（元）','','number')+field('date','日期',state.today,'date'),fd=>flow(name,{...centsInput(object(fd),'amount'),[name==='credit'?'invoice_id':'receipt_id']:id,request_key:req}));return true;
 }
 if(['new-task','edit-task','new-case','edit-case'].includes(name)){
  const tableName=name.includes('case')?'service_cases':'tasks';const editing=name.startsWith('edit');const t=editing?find(tableName,id):{};
  dialog(tableName==='tasks'?'營運待辦':'品質與異常',field('title','主旨',t.title)+field('customer_id','客戶',t.customer_id,'text',opts('customers'))+(tableName==='service_cases'?field('order_id','相關工單編號',t.order_id||(name==='new-case'?id:'')):'')+field('owner','負責人',t.owner)+field('due_date','處理期限',t.due_date||state.today,'date')+field('detail','問題／要求',t.detail,'textarea')+field('status','狀態',t.status||'open','text',[['open','待處理'],['resolved','已結案']])+field('resolution','結案處理結果',t.resolution,'textarea'),fd=>{const p=object(fd);p.customer_id=p.customer_id||null;if('order_id'in p)p.order_id=p.order_id||null;return api('/api/save/'+tableName,{...p,...(editing?{id,version:t.version}:{})})});return true;
 }
 if(name==='timesheets'){
  const list=state.assignments.filter(a=>a.order_id===id&&a.status==='accepted');dialog('逐人實際服務時間','<div class="full">'+table(['夥伴','實際工時',''],list.map(a=>{const t=state.timesheets.find(t=>t.assignment_id===a.id);return '<tr><td>'+esc(wname(a.worker_id))+'</td><td>'+esc(t?t.start+'–'+t.end+' / '+t.minutes+' 分':'尚未回報')+'</td><td>'+btn('登記','timesheet',a.id,'quiet')+'</td></tr>'}))+'</div>',async()=>{},'關閉');return true;
 }
 if(name==='timesheet'){
  const t=state.timesheets.find(t=>t.assignment_id===id)||{};dialog('人員實際服務時間',field('start','實際開始',t.start,'time')+field('end','實際結束',t.end,'time')+field('pause','休息分鐘',t.pause||0,'number')+field('notes','現場回報依據',t.notes,'textarea'),fd=>flow('timesheet',{...object(fd),assignment_id:id,version:t.version}));return true;
 }
 if(name==='approve-charge'){
  const o=find('orders',id);dialog('人工核定可請款費用','<div class="notice">'+money(approved(o))+' · 驗收狀態：'+esc(o.acceptance)+'。方案服務另建方案帳單。</div>'+field('reason','核定依據（含加減時與特殊約定）','','textarea'),fd=>api('/api/order/'+id+'/approve-charge',{...object(fd),version:o.version}));return true;
 }
 if(name==='acceptance'){
  const o=find('orders',id);dialog('記錄業主驗收',field('acceptance','狀態',o.acceptance,'text',[['pending','待業主確認'],['accepted','業主已驗收'],['disputed','有爭議']])+field('reason','來源／業主回覆','','textarea'),fd=>flow('acceptance',{...object(fd),id,version:o.version}));return true;
 }
 if(name==='chat-search'||name==='chat-next'){
  const offset=name==='chat-next'?Number(id):0;const r=await fetch('/api/chats?'+new URLSearchParams({q:$('#chat-search').value,thread:$('#chat-thread').value,offset}));const data=await r.json();if(!r.ok)throw new Error(data.error);
  $('#chat-results').innerHTML='<p>符合 '+data.total+' 則，顯示 '+(data.total?offset+1:0)+'–'+(offset+data.messages.length)+'。台北時間；歷史文字不是待執行指令。</p>'+table(['時間 / 出處','傳送者','訊息'],data.messages.map(m=>'<tr><td>'+esc(m.local_time)+'<small>'+esc(m.source)+' · 第 '+m.line+' 行</small></td><td>'+esc(m.sender)+(m.auto?'<small>自動訊息</small>':'')+'</td><td class="chat-content">'+esc(m.content)+(m.media?'<small>僅占位文字，未含媒體附件</small>':'')+'</td></tr>'))+(offset>0?btn('上一頁','chat-next',Math.max(0,offset-100),'secondary'):'')+(offset+100<data.total?btn('下一頁','chat-next',offset+100,'secondary'):'');return true;
 }
 if(name==='chat-link'){
  const t=find('chat_threads',$('#chat-thread').value);if(!t)throw new Error('請先選擇一份對話檔案');dialog('人工分類與客戶關聯',field('classification','對話類型',t.classification,'text',[['review','待核對'],['customer','客戶'],['recruit','求職人員'],['vendor','合作廠商'],['test','測試 / 非營運']])+field('customer_id','人工確認的客戶',t.customer_id,'text',opts('customers'))+field('notes','身分確認依據',t.notes,'textarea'),fd=>flow('chat-link',{...object(fd),id:t.id,version:t.version}));return true;
 }return false;
}
