$$('.resource-search').forEach(input=>input.oninput=()=>{const q=input.value.trim().toLowerCase();$$('#'+input.dataset.resourceTarget+' [data-resource-row]').forEach(row=>row.style.display=row.textContent.toLowerCase().includes(q)?'':'none')});
on('#save-tenant','click',()=>toast('租户设置已保存'));
on('#invite-member-page','click',()=>toast('下一步可增加成员选择与角色分配表单'));
on('#add-role','click',()=>toast('下一步可增加自定义角色表单'));
on('#add-user','click',()=>toast('下一步可增加平台用户创建表单'));
$$('[data-role-save]').forEach(button=>button.onclick=()=>toast('角色权限已保存并写入审计记录'));
