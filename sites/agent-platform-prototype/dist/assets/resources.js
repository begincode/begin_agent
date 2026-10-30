$$('.resource-search').forEach(input=>input.oninput=()=>{const q=input.value.trim().toLowerCase();$$('#'+input.dataset.resourceTarget+' [data-resource-row]').forEach(row=>row.style.display=row.textContent.toLowerCase().includes(q)?'':'none')});
function filterToolRows(){const q=$('#tool-search').value.trim().toLowerCase(),group=$('#tool-group-filter').value;let count=0;$$('#tool-rows [data-resource-row]').forEach(row=>{const visible=(!q||row.textContent.toLowerCase().includes(q))&&(group==='all'||row.dataset.toolGroup===group);row.style.display=visible?'':'none';if(visible)count++});$('#tool-count').textContent=`${count} 个工具`}
if($('#tool-search')){
$('#tool-search').oninput=filterToolRows;$('#tool-group-filter').onchange=filterToolRows;
}

const additions = {'#add-model':'下一步可增加模型配置表单','#add-tool':'下一步可增加工具配置表单','#add-mcp':'下一步可增加 MCP 连接表单','#add-skill':'下一步可增加 Skill 安装流程'};
for (const [selector,message] of Object.entries(additions)) on(selector,'click',()=>toast(message));
