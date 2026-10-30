let currentTask=tasks[0], taskFilter="all";
function renderTasks(){if (!$("#task-tbody")) return;const q=$('#task-search')?.value.trim().toLowerCase()||'';const list=tasks.filter(t=>(taskFilter==='all'||t.status===taskFilter)&&(!q||(t.title+t.code).toLowerCase().includes(q)));$('#task-total').textContent=tasks.length;for(const button of $$('[data-task-filter]')){const count=button.dataset.taskFilter==='all'?tasks.length:tasks.filter(t=>t.status===button.dataset.taskFilter).length;button.querySelector('b').textContent=count;}$('#task-count').textContent=`${list.length} 个任务`;$('#task-tbody').innerHTML=list.map(t=>`<tr><td><b>${escapeHtml(t.title)}</b><div class="sub">${escapeHtml(t.code)}</div></td><td>${escapeHtml(t.agent)}</td><td>${taskBadge(t.status)}</td><td><div class="progress"><i style="width:${t.progress}%"></i></div><div class="sub" style="margin-top:4px">${t.progress}%</div></td><td>${escapeHtml(t.time)}</td><td><button class="link" data-task-code="${escapeHtml(t.code)}">查看</button></td></tr>`).join('')||'<tr><td colspan="6" class="empty">没有匹配的任务</td></tr>';$$('[data-task-code]').forEach(b=>b.onclick=()=>openTask(b.dataset.taskCode))}
function ensureTaskConversation(task){if(!task.messages){task.messages=[{role:'user',content:task.input,time:task.time},{role:'agent',content:task.result,time:task.status==='running'?'处理中':'已回复'}];task.turns=1}}
function renderTaskConversation(){ensureTaskConversation(currentTask);$('#task-turn-count').textContent=`${currentTask.turns||1} 轮对话`;$('#task-chat-body').innerHTML=currentTask.messages.map(m=>`<div class="task-message ${m.role}"><div class="task-avatar">${m.role==='user'?'我':currentTask.agent.slice(0,1)}</div><div class="task-message-main"><div class="task-message-meta">${m.role==='user'?'你':currentTask.agent} · ${m.time||'刚刚'}</div><div class="task-message-content">${escapeHtml(m.content)}</div></div></div>`).join('');$('#task-chat-body').scrollTop=$('#task-chat-body').scrollHeight}
function renderTaskRunTree(){const turns=currentTask.turns||1;const status=currentTask.status==='running'?'执行中':'已完成';$('#runtime-turn-stat').textContent=turns;$('#runtime-run-stat').textContent=turns+1;$('#runtime-token-stat').textContent=`${(6.2+turns*2.2).toFixed(1)}K`;$('#task-run-tree').innerHTML=`<div class="run-node"><b>${escapeHtml(currentTask.agent)}</b><span>${escapeHtml(currentTask.code)} · 会话根任务</span></div>`+Array.from({length:turns},(_,i)=>`<div class="run-node child"><b>第 ${i+1} 轮对话</b><span>run_${currentTask.code.slice(-5)}_${i+1} · ${i===turns-1?status:'已完成'}</span></div>`).join('')}
function updateTaskDetailStatus(){$('#task-detail-status').outerHTML=taskBadge(currentTask.status).replace('<span ','<span id="task-detail-status" ')}
function openTask(code){if(pageName!=="task-detail"){navigate("task-detail.html",{code});return;}currentTask=tasks.find(t=>t.code===code)||tasks[0];ensureTaskConversation(currentTask);$('#task-detail-title').textContent=currentTask.title;$('#task-detail-code').textContent=currentTask.code;$('#task-chat-agent').textContent=`与 ${escapeHtml(currentTask.agent)} 对话`;$('#task-workspace').classList.remove('run-panel-hidden');$('#task-runtime-toggle-text').textContent='隐藏执行链';updateTaskDetailStatus();renderTaskConversation();renderTaskRunTree();saveDemoState()}
on('#submit-task','click',()=>{const title=$('#new-task-title').value.trim(),input=$('#new-task-input').value.trim();if(!title||!input){toast('请填写任务名称和第一条消息');return}const agent=$('#new-task-agent').value,result='任务已经收到，我正在处理第一条消息。你可以继续补充要求。';const task={code:'tsk_01J'+Math.random().toString(36).slice(2,8).toUpperCase(),title,agent,status:'running',progress:15,time:'刚刚',input,result,turns:1,messages:[{role:'user',content:input,time:'刚刚'},{role:'agent',content:result,time:'刚刚'}]};tasks.unshift(task);saveDemoState();openTask(task.code)});
function sendTaskFollowup(){const input=$('#task-followup-input'),message=input.value.trim();if(!message){toast('请输入要继续发送的内容');return}const task=currentTask;ensureTaskConversation(task);task.messages.push({role:'user',content:message,time:'刚刚'});task.turns=(task.turns||1)+1;task.status='running';task.progress=55;task.result='正在结合历史上下文处理新的消息。';input.value='';$('#task-followup-send').disabled=true;renderTaskConversation();renderTaskRunTree();updateTaskDetailStatus();renderTasks();setTimeout(()=>{const reply=`我已结合前 ${task.turns-1} 轮上下文继续处理。本轮补充要求是“${message.slice(0,36)}${message.length>36?'…':''}”。相关结论已经合并到当前任务，并保留了本轮 Run 记录。`;task.messages.push({role:'agent',content:reply,time:'刚刚'});task.result=reply;task.status='completed';task.progress=100;if(currentTask===task){renderTaskConversation();renderTaskRunTree();updateTaskDetailStatus();$('#task-followup-send').disabled=false}renderTasks();saveDemoState();toast('Agent 已回复，本轮 Run 已完成')},650)}
if (pageName === 'tasks') {
  $$('[data-task-filter]').forEach(button => button.onclick = () => {
    taskFilter = button.dataset.taskFilter;
    $$('[data-task-filter]').forEach(item => item.classList.toggle('active', item === button));
    renderTasks();
  });
  on('#task-search', 'input', renderTasks);
  renderTasks();
}
if (pageName === 'task-create') {
  $('#new-task-agent').innerHTML = agents.filter(agent => agent.status === 'published').map(agent => `<option value="${escapeHtml(agent.name)}">${escapeHtml(agent.name)}</option>`).join('');
}
if (pageName === 'task-detail') {
  openTask(params.get('code'));
  on('#task-retry', 'click', () => {
    currentTask.status = 'running'; currentTask.progress = 10; currentTask.result = '任务已经重新进入执行队列。';
    openTask(currentTask.code); toast('任务已重新执行');
  });
  on('#task-followup-send', 'click', sendTaskFollowup);
  on('#task-followup-input', 'keydown', event => {if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') sendTaskFollowup();});
  on('#task-runtime-toggle', 'click', () => {
    const hidden = $('#task-workspace').classList.toggle('run-panel-hidden');
    $('#task-runtime-toggle-text').textContent = hidden ? '显示执行链' : '隐藏执行链';
  });
}
