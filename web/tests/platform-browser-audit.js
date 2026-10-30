async (page) => {
  const root = 'http://127.0.0.1:18030/agent';
  const results = [], errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const request = async (path, body) => {
    const r = await page.request.post(root + '/api/v1' + path, {data: body});
    if (r.status() !== 201) throw new Error(path + ': ' + await r.text());
    return r.json();
  };
  const profile = await request('/model-profiles', { provider_code: 'deepseek', display_name: '浏览器验收模型 · ' + '长名称'.repeat(10), model_code: 'deepseek-flash', base_url: 'https://api.deepseek.com', api_key: 'browser-fixture-key-not-real' });
  await request('/tools', {tool_name: '文本统计', implementation: 'text_stats'});
  await page.reload();
  await page.getByRole('heading', {name: 'Agent 工作台', exact: true}).waitFor();
  await page.getByLabel('Agent 名称', {exact: true}).fill('浏览器验收助手');
  await page.getByRole('button', {name: '创建 Agent', exact: true}).click();
  await page.getByText('Agent 已创建，请发布首个版本').waitFor();
  await page.getByLabel('系统提示词', {exact: true}).fill('按任务要求给出可核对的结果。');
  await page.getByRole('button', {name: '发布新版本', exact: true}).click();
  await page.getByText('版本已发布；后续任务使用此版本快照').waitFor();
  await page.getByRole('button', {name: '任务与运行', exact: true}).click();
  await page.getByLabel('任务标题', {exact: true}).fill('浏览器链路验收');
  await page.getByLabel('任务要求', {exact: true}).fill('仅验证任务提交、运行查询和取消，不调用外部模型。');
  await page.getByRole('button', {name: '提交任务', exact: true}).click();
  await page.getByText('任务已提交，等待 Worker 领取').waitFor();
  await page.getByRole('button', {name: '取消任务', exact: true}).click();
  await page.getByText('任务已取消', {exact: true}).waitFor();
  await page.screenshot({path: 'output/playwright/agent-v1/task-cancelled.png'});
  const widths = [[320,800],[390,844],[768,1024],[1280,720],[844,390],[390,360]];
  for (const [width,height] of widths) {
    await page.setViewportSize({width,height});
    for (const tab of ['Agent','任务与运行','工具','成员','租户','对话调试']) {
      await page.getByRole('navigation').getByRole('button', {name:tab,exact:true}).click();
      await page.waitForFunction(() => !document.querySelector('main[aria-busy="true"]'));
      const metrics = await page.evaluate(() => {
        const visible = el => {const r=el.getBoundingClientRect();return r.width>0&&r.height>0};
        const rect = el => {const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};
        const controls = [...document.querySelectorAll('button,input:not([type=checkbox]),select,summary')].filter(visible).map(el=>({tag:el.tagName,text:(el.textContent||el.getAttribute('aria-label')||'').slice(0,24),...rect(el),radius:getComputedStyle(el).borderRadius}));
        const composer=document.querySelector('.composer-area');
        return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,controls,composer:composer?rect(composer):null,logo:rect(document.querySelector('.platform-logo'))};
      });
      if(metrics.scrollWidth>width+1 || metrics.scrollHeight>height+1) throw new Error('Page overflow '+width+'x'+height+' '+tab+' '+JSON.stringify(metrics));
      if(metrics.controls.some(c=>Math.abs(c.height-40)>.5||c.radius!=='8px')) throw new Error('Control geometry '+width+'x'+height+' '+tab+' '+JSON.stringify(metrics));
      if(metrics.composer && metrics.composer.bottom>height+1) throw new Error('Composer outside viewport '+width+'x'+height+' '+JSON.stringify(metrics));
      results.push({tab,...metrics});
      if(tab==='Agent'||tab==='对话调试') await page.screenshot({path:`output/playwright/agent-v1/${width}x${height}-${tab==='Agent'?'agents':'chat'}.png`});
    }
  }
  await page.setViewportSize({width:390,height:844});
  const markdown='# 结果\n\n|项目|说明|\n|---|---|\n|'+('wide_cell_'.repeat(30))+'|值|\n\n```python\n'+('long_code_'.repeat(80))+'\n```';
  await page.route('**/api/v1/chat/stream', route => route.fulfill({status:200,contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'done',response:{message:{content:markdown},finish_reason:'stop',usage:{total_tokens:30}}})+'\n\n'}));
  await page.getByLabel('对话消息').fill('浏览器模拟模型回复，检查长内容');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.getByText('结果',{exact:true}).waitFor();
  const overflow = await page.evaluate(() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,card:document.querySelector('.message.assistant').getBoundingClientRect().width,copyButtons:document.querySelectorAll('.md-copy-button').length}));
  if(overflow.scrollWidth>overflow.width+1||!overflow.copyButtons) throw new Error('Long content overflow');
  await page.screenshot({path:'output/playwright/agent-v1/chat-long-content.png'});
  await page.evaluate(data => { window.__agentV1Audit = data }, {results,longContent:overflow,pageErrors:errors,modelResponse:'mocked; no provider calls'});
  if(errors.length) throw new Error(errors.join('\n'));
  return {checks:results.length,viewports:widths,longContent:overflow,pageErrors:errors};
}
