/* Uses the panel's token session and write guard. No credentials in storage. */
let accountCatalog=null, accountDraft=null, accountRevision=null, accountIsNew=false;
let accountDirty=false, accountBusy=false;
const copyAccount=value=>JSON.parse(JSON.stringify(value));
const ACCOUNT_STATUS={ACTIVE:'正常',DISABLED:'停用',FROZEN:'冻结',CLOSED:'关闭',LOST:'挂失',CANCELLED:'已取消',CANDIDATE:'待核实',
  PENDING:'等待处理',WAITING_USER:'等待用户确认',SUCCEEDED:'处理完成',FAILED:'处理失败',HUMAN_TAKEOVER:'需要人工处理'};
const accountSections={
  cards:{title:'银行卡',fields:{card_id:'卡片编号',label:'卡片名称',last4:'尾号',number_masked:'脱敏卡号',type:'卡类型',status:'状态',daily_limit:'日额度'},
    create:()=>({card_id:'CARD-'+crypto.randomUUID().slice(0,8).toUpperCase(),label:'演示借记卡',last4:'0000',number_masked:'**** **** **** 0000',type:'借记卡',status:'ACTIVE',daily_limit:5000,channels:{ONLINE:true,OVERSEAS:false}})},
  contacts:{title:'联系人',fields:{contact_id:'联系人编号',name:'姓名',phone:'手机号',account_id:'关联账户（可留空）',relationship:'关系代码',birthday:'生日（可留空）'},
    create:()=>({contact_id:'CT-'+crypto.randomUUID().slice(0,8),owner_id:accountDraft.user_id,name:'',phone:'',account_id:null,relationship:'OTHER',birthday:null,link_verification:'PROVISIONAL',source:'PANEL',created_at:new Date().toISOString()})},
  transactions:{title:'交易流水',fields:{tx_id:'流水号',merchant:'商户／对方姓名',amount:'金额',category:'类别',direction:'方向（IN／OUT）',time:'时间',counterparty_id:'对方账户（可留空）',card_id:'关联卡片（可留空）'},
    create:()=>({tx_id:'TX-'+crypto.randomUUID().slice(0,8),account_id:accountDraft.user_id,merchant:'演示消费',amount:0,category:'餐饮',direction:'OUT',time:new Date().toISOString()})},
  subscriptions:{title:'订阅登记',fields:{subscription_id:'订阅编号',merchant:'订阅名称',amount:'每期金额',next_charge:'计划日期',status:'状态'},
    create:()=>({subscription_id:'SUB-'+crypto.randomUUID().slice(0,8),merchant:'演示会员',amount:0,next_charge:new Date(Date.now()+86400000).toISOString().slice(0,10),status:'ACTIVE'})},
  holdings:{title:'理财持仓',fields:{holding_id:'持仓编号',product_id:'产品编号',product_name:'产品名称',principal:'本金',estimated_value:'估值',purchased_at:'购买时间'},
    create:()=>({holding_id:'HLD-'+crypto.randomUUID().slice(0,8),product_id:accountCatalog.products[0]?.product_id||'',product_name:accountCatalog.products[0]?.name||'',principal:0,estimated_value:0,purchased_at:new Date().toISOString()})},
  tasks:{title:'待办任务',fields:{task_id:'任务编号',task_type:'类型代码',run_at:'计划时间（可留空）',status:'状态',max_attempts:'最多尝试次数'},
    create:()=>({task_id:'TSK-'+crypto.randomUUID().slice(0,8),owner_id:accountDraft.user_id,task_type:'REMINDER',payload:{message:'演示提醒'},run_at:new Date(Date.now()+86400000).toISOString(),depends_on:[],idempotency_key:'panel:'+crypto.randomUUID(),status:'PENDING',attempts:0,max_attempts:3,error:null,created_at:new Date().toISOString(),updated_at:new Date().toISOString()})}
};
const advancedAccountSections={
  risk_profile:['风险测评档案','无档案填 null；已有档案可设置 risk_profile（C1–C5）、assessed_at 等字段。'],
  locks:['锁资明细','对象，以 lock_id 为键；user_id 必须是当前账户，生效锁资合计须等于锁定金额。'],
  schedules:['预约转账','数组，user_id 必须是当前账户；请同时维护相应待办。'],
  splits:['AA 收款安排','数组，user_id 必须是当前账户；不会撤回他人已付款项。'],
  orders:['模拟订单','数组，user_id 必须是当前账户。'],
  disputes:['争议工单','数组，owner_id 必须是当前账户，关联本账户流水与卡片。'],
  outbox:['通知记录','数组，owner_id 必须是当前账户。'],
  card_spent:['卡片当日累计支付','对象，以本账户 card_id 为键，值为金额。'],
  card_spent_date:['卡片累计支付日期','对象，以 card_id 为键，值为 YYYY-MM-DD。'],
  risk_attempts:['测评次数记录','数组，值为 Unix 秒时间戳；不需要时填 []。']
};
function accountInput(key,title,value,numeric=false){
  return `<label>${esc(title)}<input data-account-field="${esc(key)}" type="${numeric?'number':'text'}" ${numeric?'min="0" step="0.01"':''} value="${esc(value??'')}"></label>`;
}
async function loadAccounts(force=false){
  if(accountBusy&&!force)return;
  if(accountDirty&&!confirm('丢弃未保存的编辑并重新加载？'))return;
  accountDirty=false;accountCatalog=await api('/api/accounts');accountRevision=accountCatalog.revision;
  $('#tab-accounts').innerHTML=`<p class="sub">管理虚构账户与演示场景。保存或重置会自动备份，并短暂重载银行服务；所有手机需重新登录。初始状态包含密码及业务数据，历史回执与审计保留。</p>
    <div class="account-layout"><aside class="card"><div class="row"><button class="btn primary" id="newAccount">新增账户</button><button class="btn" id="refreshAccounts">刷新</button></div>
    <input id="accountSearch" aria-label="搜索账户" placeholder="搜索姓名、账号或手机号" style="width:100%"><div class="account-list" id="accountList"></div>
    <button class="btn" id="initializeAccounts" style="margin-top:12px">为未初始化账户保存初始状态</button></aside>
    <section id="accountEditor" class="card account-form"><p class="muted">从左侧选择账户，或新增账户。</p></section></div>`;
  renderAccountList('');$('#accountSearch').oninput=e=>renderAccountList(e.target.value);
  $('#newAccount').onclick=()=>{if(accountDirty&&!confirm('丢弃未保存的修改并新增账户？'))return;
    accountDraft=copyAccount(accountCatalog.template);accountIsNew=true;accountDirty=false;renderAccountEditor(null);};
  $('#refreshAccounts').onclick=()=>loadAccounts().catch(e=>toast(e.message));
  $('#initializeAccounts').onclick=()=>accountAction('initialize');
}
function renderAccountList(search){
  const rows=accountCatalog.accounts.filter(a=>[a.user_id,a.name,a.phone].some(v=>String(v||'').includes(search)));
  $('#accountList').innerHTML=rows.map(a=>`<button class="btn" data-select-account="${esc(a.user_id)}"><b>${esc(a.name)}</b><br><span class="sub">${esc(a.user_id)} · ${money(a.balance)}${a.shadow?' · 收款账户':''}<br>${a.baseline_at?'已保存初始状态':'尚未保存初始状态'}</span></button>`).join('')||'<p class="muted">没有匹配账户</p>';
  document.querySelectorAll('[data-select-account]').forEach(b=>b.onclick=()=>selectAccount(b.dataset.selectAccount).catch(e=>toast(e.message)));
}
async function selectAccount(uid,force=false){
  if(accountBusy&&!force)return;if(accountDirty&&!confirm('丢弃未保存的修改并切换账户？'))return;
  const data=await api('/api/accounts/'+encodeURIComponent(uid));accountDraft=copyAccount(data.data);accountRevision=data.revision;accountIsNew=false;accountDirty=false;
  document.querySelectorAll('[data-select-account]').forEach(b=>b.classList.toggle('selected',b.dataset.selectAccount===uid));renderAccountEditor(data.baseline_at);
}
function renderAccountEditor(baseline){
  const a=accountDraft.account;
  $('#accountEditor').innerHTML=`<h2>${accountIsNew?'新增账户':esc(a.name)+' · '+esc(accountDraft.user_id)}</h2>
    <p class="sub">${accountIsNew?'首次保存同时建立初始状态。':baseline?'初始状态保存于 '+esc(when(baseline)):'首次保存前自动记录当前状态作为初始状态。'}</p>
    <div class="account-editor-grid">${accountInput('user_id','登录账户标识',accountDraft.user_id)}${accountInput('name','姓名',a.name)}${accountInput('phone','手机号',a.phone)}${accountInput('card_no','账户号码',a.card_no)}
    ${accountInput('balance','总余额',a.balance,true)}${accountInput('locked_amount','锁定金额',a.locked_amount,true)}
    <label>账户状态<select id="accountStatus">${['ACTIVE','DISABLED','FROZEN','CLOSED'].map(v=>`<option value="${v}" ${v===a.status?'selected':''}>${ACCOUNT_STATUS[v]}</option>`).join('')}</select></label>
    <label>${accountIsNew?'登录密码':'新密码（留空保留）'}<input id="accountPassword" type="password" autocomplete="new-password" minlength="8" maxlength="512"></label>
    ${accountInput('daily_spent','当日累计转出',accountDraft.daily_spent,true)}${accountInput('daily_spent_date','累计转出日期',accountDraft.daily_spent_date)}</div>
    <details><summary>风险测评</summary><div class="account-editor-grid"><label>风险等级<select id="accountRiskGrade"><option value="">无测评档案</option>${['C1','C2','C3','C4','C5'].map((v,i)=>`<option value="${v}" ${accountDraft.risk_profile?.risk_profile===v?'selected':''}>${v} · ${['谨慎型','稳健型','平衡型','进取型','激进型'][i]}</option>`).join('')}</select></label><label>测评时间<input id="accountRiskDate" value="${esc(accountDraft.risk_profile?.assessed_at||'')}"></label></div></details>
    <div id="accountRecords"></div><details><summary>高级业务设置</summary><p class="sub">支持完整编辑记录；编号、归属和依赖须保持一致。密码摘要、授权令牌、历史回执与审计不在此编辑。</p>
    ${Object.entries(advancedAccountSections).map(([key,[title,note]])=>`<details><summary>${esc(title)}</summary><p class="sub">${esc(note)}</p><textarea data-advanced-account="${key}" aria-label="${esc(title)}">${esc(JSON.stringify(accountDraft[key],null,2))}</textarea></details>`).join('')}</details>
    <div class="error" id="accountError" role="alert" style="margin:12px 0"></div><div class="account-actions"><button class="btn primary" id="saveAccount">${accountIsNew?'创建账户并保存初始状态':'保存修改'}</button>
    ${accountIsNew?'':'<button class="btn danger" id="resetAccount">恢复初始状态</button><button class="btn" id="baselineAccount">将当前已保存状态设为初始状态</button>'}</div>`;
  const uidInput=document.querySelector('[data-account-field="user_id"]');uidInput.disabled=!accountIsNew;uidInput.oninput=()=>{accountDraft.user_id=uidInput.value.trim();accountDirty=true;};
  document.querySelectorAll('[data-account-field]').forEach(input=>{if(input===uidInput)return;input.oninput=()=>{const key=input.dataset.accountField;const v=input.type==='number'?Number(input.value):input.value;
    if(key==='daily_spent'||key==='daily_spent_date')accountDraft[key]=v===''?null:v;else accountDraft.account[key]=v;accountDirty=true;};});
  $('#accountStatus').onchange=e=>{accountDraft.account.status=e.target.value;accountDirty=true;};$('#accountPassword').oninput=()=>{accountDirty=true;};
  document.querySelectorAll('[data-advanced-account]').forEach(input=>input.oninput=()=>{accountDirty=true;});
  $('#accountRiskGrade').value=accountDraft.risk_profile?.risk_profile||'';
  const riskChange=()=>{const grade=$('#accountRiskGrade').value;accountDraft.risk_profile=grade?{...accountDraft.risk_profile,risk_profile:grade,assessed_at:$('#accountRiskDate').value||new Date().toISOString(),source:'PANEL'}:null;
    document.querySelector('[data-advanced-account="risk_profile"]').value=JSON.stringify(accountDraft.risk_profile,null,2);accountDirty=true;};
  $('#accountRiskGrade').onchange=riskChange;$('#accountRiskDate').oninput=riskChange;
  $('#saveAccount').onclick=()=>accountAction(accountIsNew?'create':'save');
  if(!accountIsNew){$('#resetAccount').onclick=()=>accountAction('reset');$('#baselineAccount').onclick=()=>accountAction('baseline');}renderAccountRecords();
}
function renderAccountRecords(){
  const expanded=new Set([...document.querySelectorAll('[data-account-section][open]')].map(d=>d.dataset.accountSection));
  $('#accountRecords').innerHTML=Object.entries(accountSections).map(([key,s])=>`<details><summary>${esc(s.title)} · ${accountDraft[key].length} 条</summary><div id="rows-${key}"></div><button class="btn" data-add-record="${key}">添加${esc(s.title)}</button>
    <details><summary>完整记录 JSON</summary><textarea data-record-json="${key}" aria-label="${esc(s.title)}完整记录">${esc(JSON.stringify(accountDraft[key],null,2))}</textarea><button class="btn" data-apply-json="${key}">应用 JSON 到表单</button></details></details>`).join('');
  for(const [key,s] of Object.entries(accountSections)){
    const section=$('#rows-'+key).parentElement;section.dataset.accountSection=key;section.open=expanded.has(key);
    $('#rows-'+key).innerHTML=accountDraft[key].map((row,i)=>`<div class="account-record"><div class="row"><b>${esc(s.title)} ${i+1}</b><button class="btn danger" data-remove-record="${key}" data-row="${i}">删除</button></div><div class="account-editor-grid">
    ${Object.entries(s.fields).map(([field,title])=>`<label>${esc(title)}${field==='status'?`<select data-record-section="${key}" data-row="${i}" data-field="${field}">${(key==='cards'?['ACTIVE','FROZEN','LOST','CLOSED']:key==='tasks'?['PENDING','WAITING_USER','SUCCEEDED','FAILED','CANCELLED','HUMAN_TAKEOVER']:['ACTIVE','CANCELLED','CANDIDATE']).map(v=>`<option value="${v}" ${row[field]===v?'selected':''}>${ACCOUNT_STATUS[v]}</option>`).join('')}</select>`:`<input data-record-section="${key}" data-row="${i}" data-field="${field}" type="${['daily_limit','amount','principal','estimated_value','max_attempts'].includes(field)?'number':'text'}" value="${esc(row[field]??'')}">`}</label>`).join('')}
    ${key==='cards'?['ONLINE','OVERSEAS'].map(channel=>`<label>${channel==='ONLINE'?'网上支付':'境外交易'}<select data-card-channel="${channel}" data-row="${i}"><option value="true" ${row.channels?.[channel]!==false?'selected':''}>开启</option><option value="false" ${row.channels?.[channel]===false?'selected':''}>关闭</option></select></label>`).join(''):''}</div></div>`).join('');
  }
  document.querySelectorAll('[data-record-section]').forEach(input=>input.oninput=()=>{const {recordSection:key,row:i,field}=input.dataset;
    accountDraft[key][i][field]=input.type==='number'?Number(input.value):(input.value||(['birthday','account_id','counterparty_id','card_id','run_at'].includes(field)?null:''));accountDirty=true;syncRecordJson(key);});
  document.querySelectorAll('[data-card-channel]').forEach(input=>input.onchange=()=>{const row=accountDraft.cards[input.dataset.row];row.channels={...row.channels,[input.dataset.cardChannel]:input.value==='true'};accountDirty=true;syncRecordJson('cards');});
  document.querySelectorAll('[data-add-record]').forEach(b=>b.onclick=()=>{try{collectAccountRecords();const key=b.dataset.addRecord;accountDraft[key].push(accountSections[key].create());accountDirty=true;renderAccountRecords();}catch(e){$('#accountError').textContent=e.message;}});
  document.querySelectorAll('[data-remove-record]').forEach(b=>b.onclick=()=>{try{collectAccountRecords();accountDraft[b.dataset.removeRecord].splice(Number(b.dataset.row),1);accountDirty=true;renderAccountRecords();}catch(e){$('#accountError').textContent=e.message;}});
  document.querySelectorAll('[data-record-json]').forEach(input=>input.oninput=()=>{accountDirty=true;});
  document.querySelectorAll('[data-apply-json]').forEach(b=>b.onclick=()=>{try{const key=b.dataset.applyJson;const rows=JSON.parse(document.querySelector(`[data-record-json="${key}"]`).value);
    if(!Array.isArray(rows)||rows.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw new Error('必须为记录数组');accountDraft[key]=rows;accountDirty=true;renderAccountRecords();}catch(e){$('#accountError').textContent='JSON 格式错误：'+e.message;}});
}
function syncRecordJson(key){document.querySelector(`[data-record-json="${key}"]`).value=JSON.stringify(accountDraft[key],null,2);}
function collectAccountRecords(){
  const pending={};for(const key of Object.keys(accountSections)){const rows=JSON.parse(document.querySelector(`[data-record-json="${key}"]`).value);
    if(!Array.isArray(rows)||rows.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw new Error('完整记录 JSON 必须是对象数组');pending[key]=rows;}
  Object.assign(accountDraft,pending);
}
async function accountAction(action){
  if(accountBusy)return;const uid=action==='initialize'?'all':accountDraft?.user_id;const body={revision:accountRevision};
  try{
    if(action==='save'||action==='create'){
      collectAccountRecords();
      document.querySelectorAll('[data-advanced-account]').forEach(t=>{accountDraft[t.dataset.advancedAccount]=JSON.parse(t.value);});
      if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(uid||''))throw new Error('请填写有效的登录账户标识');
      if(accountIsNew){accountDraft.contacts.forEach(r=>{r.owner_id=uid;});accountDraft.transactions.forEach(r=>{r.account_id=uid;});accountDraft.tasks.forEach(r=>{r.owner_id=uid;});}
      body.data=accountDraft;body.password=$('#accountPassword').value||null;if(accountIsNew&&!body.password)throw new Error('请设置至少8位登录密码');
    }
    const names={create:'创建账户',save:'保存账户修改',reset:'将该账户恢复到初始状态',baseline:'用当前已保存状态替换初始状态',initialize:'为尚未初始化的账户保存当前状态'};
    if(!confirm(`${names[action]}？会自动备份并短暂重载服务，所有手机需重新登录。${action==='reset'?'该账户创建／保存初始状态后的修改将被替换，其他账户及历史回执不变。':''}${action==='baseline'?'未保存的编辑内容不会进入初始状态。':''}`))return;
    accountBusy=true;document.querySelectorAll('#tab-accounts button').forEach(b=>b.disabled=true);if($('#accountError'))$('#accountError').textContent='正在备份并保存，请稍候…';
    const response=await api(`/api/accounts/${encodeURIComponent(uid)}/${action}`,{method:'POST',body:JSON.stringify(body)});
    accountDirty=false;toast(response.result);await loadAccounts(true);if(action!=='initialize')await selectAccount(uid,true);accountBusy=false;
  }catch(e){accountBusy=false;document.querySelectorAll('#tab-accounts button').forEach(b=>b.disabled=false);if($('#accountError'))$('#accountError').textContent=e.message;else toast(e.message);}
}
window.addEventListener('beforeunload',e=>{if(accountDirty){e.preventDefault();e.returnValue='';}});
