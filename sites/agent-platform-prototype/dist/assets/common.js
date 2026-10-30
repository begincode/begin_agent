const initialAgents=[
  {code:'agt_01J8M3A2',name:'经营分析助手',desc:'拆分分析任务并汇总经营结论',type:'supervisor',model:'DeepSeek Chat',status:'published',updated:'今天 10:24',subs:['数据查询 Agent','知识检索 Agent']},
  {code:'agt_01J8K91B',name:'数据查询 Agent',desc:'查询经营指标并输出结构化数据',type:'normal',model:'公司 OpenAI 兼容模型',status:'published',updated:'昨天 16:40',subs:[]},
  {code:'agt_01J8H72C',name:'知识检索 Agent',desc:'检索内部制度和业务文档',type:'normal',model:'DeepSeek Chat',status:'published',updated:'10月20日',subs:[]},
  {code:'agt_01J8D44D',name:'报告生成 Agent',desc:'将分析结果整理为报告',type:'normal',model:'Claude Sonnet',status:'draft',updated:'10月18日',subs:[]}
];
const runs=[
  {title:'分析本月销售下滑原因',agent:'经营分析助手',status:'成功',time:'10分钟前'},
  {title:'整理新版员工制度',agent:'知识检索 Agent',status:'运行中',time:'12分钟前'},
  {title:'生成接口变更说明',agent:'研发协作助手',status:'等待确认',time:'30分钟前'}
];
const initialTasks=[
  {code:'tsk_01JA4K2M',title:'分析本月销售下滑原因',agent:'经营分析助手',status:'completed',progress:100,time:'10分钟前',input:'分析本月各区域销售变化，定位销售下滑的主要区域、品类和可能原因，并给出下一步核查建议。',result:'销售下滑主要集中在华东区域，重点门店客流下降和两个主力品类缺货是主要影响因素。建议先核对门店库存与促销执行记录。'},
  {code:'tsk_01JA3H8N',title:'整理新版员工制度',agent:'知识检索 Agent',status:'running',progress:62,time:'12分钟前',input:'整理本周发布的员工制度变化，按照考勤、假期和报销三个主题输出摘要。',result:'任务仍在执行，已经完成制度检索和内容去重。'},
  {code:'tsk_01JA1P6Q',title:'生成接口变更说明',agent:'报告生成 Agent',status:'waiting',progress:80,time:'30分钟前',input:'根据接口差异生成面向调用方的变更说明，涉及删除字段时需要人工确认。',result:'检测到删除字段，需要确认后继续生成正式文档。'},
  {code:'tsk_01J9Z7RT',title:'复核上周经营周报',agent:'经营分析助手',status:'completed',progress:100,time:'昨天 18:20',input:'复核上周经营周报中的销售额、毛利率和缺货率，并标记数据不一致项。',result:'已完成复核，发现两处缺货率口径不一致。'}
];
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const pageName = document.body.dataset.page;
const params = new URLSearchParams(location.search);
const storageKey = 'agent-platform-prototype-v2';
function readDemoState() {
  try { if (window.name.startsWith(storageKey + ':')) return JSON.parse(window.name.slice(storageKey.length + 1)); } catch (_) {}
  try { return JSON.parse(localStorage.getItem(storageKey)) || {}; } catch (_) { return {}; }
}
const restored = readDemoState();
const agents = Array.isArray(restored.agents) && restored.agents.length ? restored.agents : initialAgents;
const tasks = Array.isArray(restored.tasks) && restored.tasks.length ? restored.tasks : initialTasks;
function saveDemoState() {
  const state = JSON.stringify({agents, tasks});
  // window.name also carries demo data between local files if storage is unavailable.
  window.name = storageKey + ':' + state;
  try { localStorage.setItem(storageKey, state); } catch (_) {}
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function on(selector, event, handler) { $(selector)?.addEventListener(event, handler); }
function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2200);
}
function pageUrl(file, query = {}) {
  const url = new URL(file, location.href);
  for (const [key, value] of Object.entries(query)) if (value != null) url.searchParams.set(key, value);
  return url.href;
}
function navigate(file, query) { saveDemoState(); location.href = pageUrl(file, query); }
function openAgent(code, tab = 'config') { navigate('agent-' + tab + '.html', {code}); }
function openTenantItem(type, name) { navigate(type + '-detail.html', {name}); }
document.addEventListener('click', event => {
  const anchor = event.target.closest('a[href]');
  if (anchor) saveDemoState();
});
window.addEventListener('pagehide', saveDemoState);
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  // Refresh a page restored from history with the latest cross-page demo state.
  const latest = readDemoState();
  if (Array.isArray(latest.agents) && latest.agents.length) agents.splice(0, agents.length, ...latest.agents);
  if (Array.isArray(latest.tasks) && latest.tasks.length) tasks.splice(0, tasks.length, ...latest.tasks);
  location.reload();
});
const appShell=$('.app'),sidebarToggle=$('#sidebar-toggle');
function setSidebarCollapsed(collapsed,persist=true){appShell.classList.toggle('sidebar-collapsed',collapsed);sidebarToggle.textContent=collapsed?'›':'‹';const label=collapsed?'展开侧边栏':'折叠侧边栏';sidebarToggle.title=label;sidebarToggle.setAttribute('aria-label',label);sidebarToggle.setAttribute('aria-expanded',String(!collapsed));if(persist){try{localStorage.setItem('agent-sidebar-collapsed',collapsed?'1':'0')}catch(e){}}}
let savedSidebarState='0';try{savedSidebarState=localStorage.getItem('agent-sidebar-collapsed')||'0'}catch(e){}setSidebarCollapsed(savedSidebarState==='1',false);sidebarToggle.onclick=()=>setSidebarCollapsed(!appShell.classList.contains('sidebar-collapsed'));
function statusBadge(s){return s==='published'?'<span class="badge green"><i class="dot"></i>已发布</span>':'<span class="badge amber"><i class="dot"></i>草稿</span>'}
function taskBadge(status){const map={completed:['green','已完成'],running:['blue','执行中'],waiting:['amber','等待确认'],pending:['gray','待执行'],failed:['amber','失败']},v=map[status]||map.pending;return `<span class="badge ${v[0]}"><i class="dot"></i>${v[1]}</span>`}
$$('[data-page-detail]').forEach(button => button.onclick = () => openTenantItem(button.dataset.pageDetail, button.dataset.pageName));
$$('[data-member-detail]').forEach(button => button.onclick = () => openTenantItem('member', button.dataset.memberDetail));
$$('[data-user-detail]').forEach(button => button.onclick = () => openTenantItem('user', button.dataset.userDetail));
