
(()=>{
  const root=document.getElementById('thvv-concept');
  const q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];
  // Metadata transcribed from SUPPORTED_DATASETS in run_eval.py at 6e7a150.
  const datasets=[
    {id:'aime25',name:'AIME25',repeats:16,batch:30},
    {id:'aime26',name:'AIME2026（已废弃）',repeats:16,batch:30},
    {id:'arxivmath',name:'ArXiv-Math',repeats:1,batch:15},
    {id:'gpqa_diamond',name:'GPQA-Diamond',repeats:3,batch:25},
    {id:'hle',name:'HLE',repeats:1,batch:60,judge:true},
    {id:'tau2_bench',name:'tau2-bench',repeats:5,batch:30},
    {id:'mmlu_pro',name:'MMLU-Pro',repeats:1,batch:40},
    {id:'simple_qa',name:'SimpleQA',repeats:1,batch:35,judge:true},
    {id:'longbench_v2',name:'LongBench V2',repeats:1,batch:10},
    {id:'live_code_bench',name:'LiveCodeBench',repeats:1,batch:15,docker:true},
    {id:'swe_bench_verified_mini_agentic',name:'SWE-Bench Verified Mini Agentic',repeats:1,batch:10,docker:true,swebench:true,enclave:true},
    {id:'swe_bench_pro',name:'SWE-Bench Pro',repeats:1,batch:1,sandbox:true,enclave:true},
    {id:'msr_vtt',name:'MSR-VTT',repeats:1,batch:5},
    {id:'mvbench',name:'MVBench',repeats:1,batch:5},
    {id:'tvbench',name:'TVBench',repeats:1,batch:5},
    {id:'math_vision',name:'MathVision',repeats:1,batch:10},
    {id:'hallusion_bench',name:'HallusionBench',repeats:1,batch:10},
    {id:'videomme_v2',name:'Video-MME-v2',repeats:1,batch:5}
  ];
  const excluded=['aime25','aime26','swe_bench_pro'];
  const buckets=['1k','9k','16k','32k','64k','128k','200k','512k','1m'];
  const fields=['th-judge-model','th-judge-url','th-sandbox','th-model','th-protocol','th-provider-name','th-perf-command','th-bucket','th-number','th-parallel','th-buckets','th-ladder','th-cooldown','th-success','th-warmup','th-tokenizer','th-limit','th-repeats','th-batch',...buckets.map(b=>'th-n-'+b)];
  const state={version:7,stage:2,mode:'perf',datasets:[],all:false,fields:{},connections:[],connectionId:'',taskId:'',replay:null,taskTab:'results'};
  let connectionDirty=false,editingId='',returnToTest=false,pendingDeleteId='';
  const connectionFields=['th-connection-name','th-api-url'];
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  q('#th-bucket-numbers').innerHTML=buckets.map(b=>'<label class="th-field">'+b+' 档请求数<input type="number" step="1" id="th-n-'+b+'" data-native="N_'+b+'" placeholder="留空沿用框架规则"></label>').join('');
  const tokenizerRules=[
    [/glm|chatglm/,'glm / chatglm','zai-org/GLM-4.6'],
    [/deepseek|deep-seek/,'deepseek / deep-seek','deepseek-ai/DeepSeek-V3'],
    [/minimax|mini-max/,'minimax / mini-max','MiniMaxAI/MiniMax-M2'],
    [/kimi|moonshot/,'kimi / moonshot','moonshotai/Kimi-K2-Thinking'],
    [/qwen|tongyi/,'qwen / tongyi','Qwen/Qwen3-235B-A22B'],
    [/llama/,'llama','meta-llama/Llama-3.1-8B-Instruct'],
    [/mistral/,'mistral','mistralai/Mistral-7B-Instruct-v0.3'],
    [/baichuan/,'baichuan','baichuan-inc/Baichuan2-13B-Chat'],
    [/yi-|yi_|yi-l/,'yi- / yi_ / yi-l','01-ai/Yi-1.5-34B-Chat'],
    [/gemma|gemini/,'gemma / gemini','google/gemma-2-2b'],
    [/claude|anthropic/,'claude / anthropic','Xenova/gpt-4o'],
    [/gpt|openai/,'gpt / openai','Xenova/gpt-4o'],
    [/phi-|phi_|phi3/,'phi- / phi_ / phi3','microsoft/Phi-3-mini-4k-instruct']
  ];
  q('#th-tokenizer-rules').innerHTML=tokenizerRules.map(([,name,value])=>'<p>'+esc(name)+' → '+esc(value)+'</p>').join('')+'<p>按模型名包含的关键词顺序匹配，不区分大小写；均未匹配时为 zai-org/GLM-4.6。Claude 使用近似 Tokenizer。</p>';
  const defaultRules={
    'th-api-url':'无默认地址，请按服务商提供的基础地址填写。',
    'th-model':'无内置默认模型，需填写或使用已配置的模型名。',
    'th-protocol':'性能压测内置默认：openai；效果评测使用 OpenAI 兼容接口。',
    'th-provider-name':'未配置时默认：unknown。',
    'th-judge-model':'未配置时默认：deepseek-v4-pro。',
    'th-judge-url':'未配置时默认：https://api.deepseek.com/v1。',
    'th-sandbox':'无默认配置；SWE-Bench Pro 要求提供此项。',
    'th-limit':'默认不指定 limit，不额外限制题数；数据集子集规则仍生效。',
    'th-repeats':'默认按所选数据集预设，具体轮次见各数据集卡片。',
    'th-batch':'默认按所选数据集预设，具体并发见各数据集卡片。',
    'th-bucket':'无默认档位，单档压测必须选择。',
    'th-number':'单档压测内置默认：500 次请求。',
    'th-parallel':'单档压测内置默认：10 并发。',
    'th-buckets':'多档内置默认：1k、9k、16k、32k、64k、128k、200k、512k。',
    'th-ladder':'多档内置默认：1、8、32、64、128，再应用各档上限与补充规则。',
    'th-cooldown':'内置默认：120 秒。',
    'th-success':'内置默认：0，关闭成功率早停。',
    'th-warmup':'内置默认：4 次，不计入测量；填 0 关闭预热。',
    'th-tokenizer':'默认按模型名匹配；未匹配时使用 zai-org/GLM-4.6。'
  };
  for(const [id,rule] of Object.entries(defaultRules)){const small=document.createElement('small');small.className='th-default';small.textContent=rule;q('#'+id).parentElement.appendChild(small);}
  function selected(){return datasets.filter(d=>state.all?!excluded.includes(d.id):state.datasets.includes(d.id));}
  function baseAddress(value){
    const url=new URL(value);
    url.pathname=url.pathname.replace(/\/+$/,'').replace(/\/(chat\/completions|messages)$/i,'')||'/';
    return url.toString();
  }
  function requestAddress(value,protocol){
    const url=new URL(baseAddress(value));
    url.pathname=url.pathname.replace(/\/+$/,'')+(protocol==='anthropic'?'/messages':'/chat/completions');
    return url.toString();
  }
  function renderDatasets(){
    q('#th-all-datasets').checked=state.all;
    q('#th-all-note').hidden=!state.all;
    const chosen=new Set(selected().map(d=>d.id));
    q('#th-datasets').innerHTML=datasets.map(d=>'<label class="th-dataset"><input type="checkbox" data-dataset="'+d.id+'" data-native="--datasets '+d.id+'"'+(chosen.has(d.id)?' checked':'')+(state.all?' disabled':'')+'><span>'+d.name+'<small>默认 '+d.repeats+' 轮 · 并发 '+d.batch+(d.judge?' · Judge':'')+'</small></span></label>').join('');
  }
  function update(){
    const perf=state.mode==='perf',all=q('#th-perf-command').value==='bench-all';
    const protocol=q('#th-protocol').value||'openai';
    const current=state.replay?.object||state.connections.find(c=>c.id===state.connectionId);
    const supported=perf||protocol!=='anthropic';
    q('#th-protocol-note').textContent=!supported?'当前框架的效果评测使用 OpenAI 兼容接口，请选择 OpenAI 兼容协议。':protocol==='anthropic'?'网页按 Anthropic 协议追加 /messages。':'网页按 OpenAI 兼容协议追加 /chat/completions。';
    let resolved='';try{if(current&&supported)resolved=requestAddress(current.url,protocol);}catch{}
    q('#th-resolved-url').hidden=!resolved;q('#th-resolved-url').textContent=resolved?'最终请求地址：'+resolved:'';
    const model=q('#th-model').value.trim().toLowerCase();
    const detected=tokenizerRules.find(([pattern])=>pattern.test(model));
    q('#th-tokenizer-rule').textContent=model?'按当前模型名推断的内置默认 Tokenizer：'+(detected?.[2]||'zai-org/GLM-4.6')+'。显式配置优先；超长档位另按下方规则。':'未填写模型名时，默认回退为 zai-org/GLM-4.6；填写后按框架关键词规则推断。';
    q('#th-single-fields').hidden=all;q('#th-matrix-fields').hidden=!all;
    const chosen=selected();
    q('#th-judge-fields').hidden=!chosen.some(d=>d.judge);
    q('#th-sandbox-field').hidden=!chosen.some(d=>d.sandbox);
    const deps=[];
    if(chosen.some(d=>d.docker||d.sandbox))deps.push('Docker');
    if(chosen.some(d=>d.swebench))deps.push('swebench');
    if(chosen.some(d=>d.enclave))deps.push('ms_enclave');
    if(chosen.some(d=>d.id==='tau2_bench'))deps.push('tau2');
    const notes=[];if(deps.length)notes.push('所选数据集依赖：'+deps.join('、')+'。');
    if(chosen.some(d=>d.id==='aime26'))notes.push('AIME2026 在框架中标记为已废弃，数学类改用 ArXiv-Math。');
    if(chosen.some(d=>d.id==='mvbench'))notes.push('MVBench 默认子集为 action_antonym。');
    if(chosen.some(d=>d.id==='tvbench'))notes.push('TVBench 默认子集为 action_count。');
    q('#th-dependencies').hidden=!notes.length;q('#th-dependencies').textContent=notes.join(' ');
    if(perf){
      const bucket=q('#th-bucket').value;
      q('#th-selection-summary').textContent=all?'执行框架多档压测':bucket?'单档压测：'+bucket:'请选择压测输入长度';
      q('#th-selection-detail').textContent=all?'输入长度、并发梯度和请求数按上方框架规则执行。':'请求数 '+(q('#th-number').value||'500（代码默认）')+' · 并发 '+(q('#th-parallel').value||'10（代码默认）')+' · 预热 '+(q('#th-warmup').value||'4（代码默认）');
    }else{
      q('#th-selection-summary').textContent=chosen.length?'已选择 '+chosen.length+' 个数据集':'请选择评测数据集';
      const r=q('#th-repeats').value,b=q('#th-batch').value,l=q('#th-limit').value;
      const base=l?'每集最多 '+l+' 题':'不设置题数上限';
      q('#th-selection-detail').textContent=chosen.length===1?base+' · '+(r||chosen[0].repeats)+' 轮 · 并发 '+(b||chosen[0].batch):base+' · '+(r?r+' 轮':'轮次按各数据集默认')+' · '+(b?'并发 '+b:'并发按各数据集默认');
    }
  }
  const statusNames={queued:'等待运行',running:'运行中',succeeded:'执行完成',failed:'执行失败',interrupted:'已中断'};
  let tasks=[],currentTask=null,loadingTask=false,tasksRequest=null,tasksKnown=false;
  let environmentJob=null,environmentBusy=false,starting=false,environmentSelection={},connectionSelection='',judgeSelection='';
  let environmentRowsSignature={};
  let environmentAction='',environmentScope='environment',cleanupPlan=null,feedbackTimer;
  const pendingButtons=new Set();
  const displayDate=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
  const activeTask=()=>tasks.find(t=>['queued','running'].includes(t.status));
  function duration(seconds){const n=Math.max(0,Math.floor(seconds||0));return (n>=3600?Math.floor(n/3600)+' 小时 ':'')+(n>=60?Math.floor(n%3600/60)+' 分 ':'')+(n%60)+' 秒';}
  function taskProgressMarkup(task,compact=false){
    const p=task.progress||{stage:statusNames[task.status],percent:task.status==='succeeded'?100:null,active:['queued','running'].includes(task.status)};
    const count=p.total?`${p.completed} / ${p.total} ${p.unit}`:'';
    const percent=p.percent===null||p.percent===undefined?null:p.percent;
    const label=[p.context,p.stage].filter(Boolean).join(' · ');
    const detail=percent===null?(p.active?'等待框架更新进度':'未取得完成数量'):percent+'%';
    return '<div class="th-task-progress'+(compact?' th-progress-compact':'')+'" data-active="'+!!p.active+'"><div class="th-progress-heading"><strong>'+esc(label)+'</strong><span>'+esc(count?count+' · '+detail:detail)+'</span></div><progress max="100"'+(percent!==null?' value="'+percent+'"':!p.active?' value="0"':'')+' aria-label="'+esc(label)+'" aria-valuetext="'+esc(count||detail)+'"></progress>'+(compact?'':'<p class="th-note">已用 '+esc(duration(p.elapsed_seconds))+(p.active?' · 显示当前阶段进度，阶段切换时重新计数；没有完成数量时仅显示进行中。':'')+'</p>')+'</div>';
  }
  function renderTaskAvailability(){
    const task=activeTask();q('#web-active-task').hidden=!task;
    if(task){q('#web-active-task-name').textContent=task.object.name+' · '+task.spec.model+' · '+task.id.slice(0,8);q('#web-active-task-progress').innerHTML=taskProgressMarkup(task);}
    environmentButtons();
  }
  function feedback(message,kind='success'){
    const box=q('#web-feedback');clearTimeout(feedbackTimer);box.dataset.kind=kind;box.textContent=message;box.hidden=false;
    feedbackTimer=setTimeout(()=>{box.hidden=true;},kind==='error'?6500:4500);
  }
  function buttonText(button,text){if(button.getAttribute('aria-busy')==='true')button.dataset.idleText=text;else button.textContent=text;}
  function buttonBusy(button,busy,label){
    const wasBusy=button.getAttribute('aria-busy')==='true';
    if(busy){if(!wasBusy)button.dataset.idleText=button.textContent;button.setAttribute('aria-busy','true');button.textContent=label;}
    else if(wasBusy){button.textContent=button.dataset.idleText;delete button.dataset.idleText;button.removeAttribute('aria-busy');}
  }
  async function withButton(button,label,action){
    const key=button.id||JSON.stringify(button.dataset);
    if(pendingButtons.has(key))return;
    pendingButtons.add(key);buttonBusy(button,true,label);button.disabled=true;
    try{return await action();}finally{pendingButtons.delete(key);buttonBusy(button,false);button.disabled=false;}
  }
  let leavingSession=false;
  function leaveSession(){leavingSession=true;root.hidden=true;window.location.replace('/login');}
  function showError(error){if(leavingSession)return;const message=String(error?.message||error);q('#web-error').textContent=message;q('#web-error').hidden=false;feedback(message,'error');}
  async function api(path,method='GET',body){
    if(leavingSession)throw new Error('请重新登录');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-THVV-Request':'1'},body:body===undefined?undefined:JSON.stringify(body)});
    if(response.status===401){leaveSession();throw new Error('请重新登录');}
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
    return data;
  }
  function save(){
    for(const id of fields)state.fields[id]=q('#'+id).value;
    try{localStorage.setItem('thvv-test-form',JSON.stringify({mode:state.mode,datasets:state.datasets,all:state.all,fields:state.fields,connectionId:state.connectionId}));}catch{}
  }
  function renderConnections(){
    const picker=q('#th-test-object');
    picker.innerHTML='<option value="">'+(state.connections.length?'请选择测试对象':'暂无测试对象')+'</option>'+state.connections.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('');
    if(state.replay){picker.innerHTML='<option value="replay">'+esc(state.replay.object.name)+'（原任务配置）</option>'+picker.innerHTML;picker.value='replay';}else picker.value=state.connectionId;
    picker.disabled=!state.connections.length&&!state.replay;
    q('#th-object-rows').innerHTML=state.connections.length?state.connections.map(c=>'<tr><td>'+esc(c.name)+(c.has_api_key?'<small class="th-note"> · 已保存密钥</small>':'')+'</td><td>'+esc(c.url)+'</td><td><div class="th-task-actions"><button type="button" class="th-task-action" data-edit-object="'+esc(c.id)+'">编辑</button><button type="button" class="th-task-action th-task-delete" data-delete-object="'+esc(c.id)+'">删除</button></div></td></tr>').join(''):'<tr><td colspan="3">暂无测试对象，填写上方信息后保存。</td></tr>';
    q('#th-connection-heading').textContent=editingId?'编辑测试对象':'新增测试对象';
    buttonText(q('#th-save-connection'),returnToTest?'保存并返回测试配置':editingId?'保存修改':'保存测试对象');
    q('#th-cancel-edit').hidden=!editingId;
    q('#web-clear-key-row').hidden=!state.connections.find(c=>c.id===editingId)?.has_api_key;
  }
  function renderStages(){
    const obj=state.replay?.object||state.connections.find(c=>c.id===state.connectionId);
    qa('[data-stage-panel]').forEach(p=>p.hidden=Number(p.dataset.stagePanel)!==state.stage);
    qa('.th-stages button').forEach(b=>{if(Number(b.dataset.stage)===(state.stage===4?3:state.stage))b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    q('#th-object-guidance').hidden=!!obj;
    q('#th-object-guidance').textContent=state.connections.length?'请选择测试对象。':'暂无测试对象，请先新增；也可先填写测试参数。';
    q('#th-current-object-detail').hidden=!obj;
    q('#th-current-object-detail').textContent=obj?obj.url+(state.replay?' · 重放将沿用原任务端点及密钥':''):'';
    q('#web-judge-key').placeholder=state.replay?'留空沿用原任务裁判密钥':'填写裁判模型密钥';
  }
  function render(){
    const perf=state.mode==='perf';
    qa('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode)));
    q('#th-perf-panel').hidden=!perf;q('#th-eval-panel').hidden=perf;q('#th-provider-field').hidden=perf;
    renderConnections();renderDatasets();update();renderStages();
  }
  async function loadObjects(){state.connections=await api('/objects');if(!state.connections.some(c=>c.id===state.connectionId))state.connectionId='';connectionSelection='';q('#web-connection-result').textContent='尚未检查';renderConnections();renderStages();update();}
  function editConnection(id){
    const c=state.connections.find(c=>c.id===id);editingId=c?.id||'';
    q('#th-connection-name').value=c?.name||'';q('#th-api-url').value=c?.url||'';q('#web-api-key').value='';q('#web-clear-key').checked=false;
    q('#web-api-key').placeholder=c?.has_api_key?'已保存；留空保持原密钥':'可留空';renderConnections();
  }
  async function saveConnection(){
    const b=q('#th-save-connection');b.disabled=true;
    try{
      const body={name:q('#th-connection-name').value.trim(),url:q('#th-api-url').value.trim()};
      const key=q('#web-api-key').value;
      if(q('#web-clear-key').checked)body.api_key='';else if(key||!editingId)body.api_key=key;
      const obj=await api('/objects'+(editingId?'/'+editingId:''),editingId?'PATCH':'POST',body);
      if(returnToTest){state.connectionId=obj.id;state.replay=null;state.stage=2;returnToTest=false;}
      editConnection('');await loadObjects();q('#th-connection-message').textContent='测试对象已保存。';save();feedback('测试对象已保存');
    }finally{b.disabled=false;}
  }
  async function deleteConnection(){
    if(!pendingDeleteId)return;
    await api('/objects/'+pendingDeleteId,'DELETE');
    if(editingId===pendingDeleteId)editConnection('');pendingDeleteId='';q('#th-delete-object-confirm').hidden=true;await loadObjects();save();feedback('测试对象已删除');
  }
  async function loadTasks(){
    if(tasksRequest)return tasksRequest;
    tasksRequest=(async()=>{
      tasks=await api('/tasks');q('#web-history-rows').innerHTML=tasks.length?tasks.map(t=>'<tr><td>'+esc(t.id.slice(0,8))+'</td><td>'+esc(t.object.name)+'<small class="th-note" style="display:block">'+esc(t.spec.model)+'</small></td><td>'+(t.spec.mode==='perf'?'性能压测':'效果评测')+'</td><td>'+esc(displayDate(t.created))+'</td><td>'+esc(statusNames[t.status]||t.status)+(t.progress?taskProgressMarkup(t,true):'')+'</td><td><div class="th-task-actions"><button type="button" class="th-task-action" data-view-task="'+t.id+'">查看</button><button type="button" class="th-task-action" data-replay-task="'+t.id+'">重放</button><button type="button" class="th-task-action th-task-delete" data-delete-task="'+t.id+'"'+(['queued','running'].includes(t.status)?' disabled':'')+'>删除</button></div></td></tr>').join(''):'<tr><td colspan="6">暂无测试任务。</td></tr>';
      tasksKnown=true;renderTaskAvailability();
    })();
    try{return await tasksRequest;}catch(error){tasksKnown=false;environmentButtons();throw error;}finally{tasksRequest=null;}
  }
  function taskBody(){
    const options={};for(const id of fields)if(!['th-model','th-protocol'].includes(id)&&q('#'+id).value.trim())options[id.slice(3)]=q('#'+id).value.trim();
    const body={object_id:state.replay?.object.id||state.connectionId,model:q('#th-model').value.trim(),mode:state.mode,protocol:q('#th-protocol').value||'openai',datasets:state.datasets,all:state.all,options};
    if(q('#web-judge-key').value)body.judge_api_key=q('#web-judge-key').value;
    return body;
  }
  async function startTask(){
    if(starting)return;
    const b=q('#web-start');starting=true;b.disabled=true;q('#web-error').hidden=true;
    const body=taskBody(),replayId=state.replay?.id;
    try{
      await loadTasks();
      if(activeTask())throw new Error('已有测试任务正在进行，请等待结束后再开始测试');
      if(!body.object_id||!body.model)throw new Error('请选择测试对象并填写模型名称');
      buttonBusy(b,true,'正在检查环境…');
      const ready=await api('/environment/readiness','POST',environmentBody());
      for(const scope of ['environment','data']){
        if(ready[scope])continue;
        buttonBusy(b,true,scope==='environment'?'正在检查环境…':'正在检查数据…');
        const result=await runEnvironment(false,scope);
        if(!result.ready)throw new Error(scope==='environment'?'运行环境尚未就绪，可在运行环境中点击“准备缺失项”':'数据尚未就绪，可在数据分组中点击“准备缺失项”');
      }
      if(JSON.stringify(body)!==JSON.stringify(taskBody())||replayId!==state.replay?.id)throw new Error('配置已变更，请按新配置重新开始');
      buttonBusy(b,true,state.mode==='eval'&&selected().some(d=>d.judge)?'正在检查模型与 Judge 连通性…':'正在检查连通性…');
      const task=await api(replayId?'/tasks/'+replayId+'/replay':'/tasks','POST',body);
      state.replay=null;q('#web-judge-key').value='';judgeSelection='';q('#web-judge-connection-result').textContent='尚未检查';await viewTask(task.id);await loadTasks();save();feedback('测试任务已创建，可查看运行状态');
    }finally{starting=false;buttonBusy(b,false);environmentButtons();loadTasks().catch(showError);}
  }
  function environmentBody(){const body=taskBody();return {mode:body.mode,model:body.model,datasets:body.datasets,all:body.all,options:body.options};}
  function connectionBody(){return {object_id:state.connectionId,replay_id:state.replay?.id||null,model:q('#th-model').value.trim(),protocol:q('#th-protocol').value||'openai'};}
  function judgeBody(){
    const body={model:q('#th-judge-model').value.trim(),url:q('#th-judge-url').value.trim(),replay_id:state.replay?.id||null};
    if(q('#web-judge-key').value)body.api_key=q('#web-judge-key').value;
    return body;
  }
  function environmentButtons(){
    const busy=environmentBusy||environmentJob?.status==='running';
    const action=environmentBusy?environmentAction:environmentJob?.action;
    const activeScope=environmentBusy?environmentScope:environmentJob?.scope;
    for(const scope of ['environment','data']){
      for(const name of ['check','prepare','clean']){
        const b=q('#web-'+name+'-'+scope);
        const active=activeScope===scope&&action===name;
        buttonBusy(b,busy&&active,{check:'检查中…',prepare:'准备中…',clean:'清理中…'}[name]);
        b.disabled=busy||starting;
      }
    }
    q('#web-start').disabled=busy||starting||!tasksKnown||!!activeTask();
    if(!starting)buttonText(q('#web-start'),!tasksKnown?'正在确认任务状态…':activeTask()?'等待当前任务结束':'开始测试');
    q('#web-confirm-cleanup').disabled=busy||starting||!cleanupPlan?.items.length;
  }
  function renderEnvironment(){
    environmentButtons();
    const names={checking:'检查中',preparing:'准备中',ready:'已就绪',online:'仍需联网',missing:'未就绪',blocked:'等待依赖',manual:'需手动准备'};
    for(const scope of ['environment','data']){
      const job=environmentJob?.groups?.[scope];
      if(!job){q('#web-'+scope+'-status').textContent='尚未检查';q('#web-'+scope+'-items').innerHTML='';q('#web-'+scope+'-log-panel').hidden=true;delete environmentRowsSignature[scope];continue;}
      const changed=scope==='data'&&environmentSelection[scope]&&environmentSelection[scope]!==JSON.stringify(environmentBody());
      const seconds=Math.max(0,(job.finished||Date.now()/1000)-(job.started||Date.now()/1000));
      q('#web-'+scope+'-status').textContent=(changed?'配置已变更，以下为上次选择的检查结果。':'')+job.message+' · '+(job.finished?'用时 '+seconds.toFixed(1)+' 秒':'已用 '+Math.floor(seconds)+' 秒');
      if(scope==='environment'&&job.ready)q('#web-status').hidden=true;
      const signature=JSON.stringify(job.items);
      if(signature!==environmentRowsSignature[scope]){
      const expanded=new Set(qa('[data-env-detail]').filter(el=>el.open).map(el=>el.dataset.envDetail));
      const rows=job.items.map(item=>{
        const versions=(item.versions||[]).map(v=>'<tr><td>'+esc(v.name)+'</td><td>'+esc(v.required)+(v.install_constraint&&v.install_constraint!==v.required?'<small class="th-default" style="display:block">安装约束：'+esc(v.install_constraint)+'</small>':'')+'</td><td>'+esc(v.installed||'未安装')+(v.source_revision?'<small class="th-default" style="display:block">来源：'+esc(v.source_revision)+'</small>':'')+'</td><td>'+esc(v.matches?'符合':'不符合')+'</td></tr>').join('');
        let disclosure='';
        if(versions||item.error_details){
          disclosure='<details class="th-env-details" data-env-detail="'+esc(item.id)+'"'+(expanded.has(item.id)?' open':'')+'><summary>'+(versions?'查看版本信息':'查看错误详情')+'</summary>';
          if(versions)disclosure+='<div class="th-table-wrap"><table class="th-env-versions"><thead><tr><th>组件</th><th>框架要求</th><th>本机版本</th><th>结果</th></tr></thead><tbody>'+versions+'</tbody></table></div><p class="th-note">按本地框架 6e7a150 的依赖文件核对。安装约束若不同，会另外列出。</p>';
          if(item.error_details)disclosure+='<pre>'+esc(item.error_details)+'</pre>';
          disclosure+='</details>';
        }
        const disposition={reused:job.action==='check'?'可直接复用':'复用已有',prepared:'本次准备'}[item.disposition];
        return '<tr><td>'+esc(item.label)+'</td><td><span class="th-env-state" data-env-state="'+esc(item.state)+'">'+esc(names[item.state]||item.state)+'</span>'+(disposition?'<small class="th-default" style="display:block">'+disposition+'</small>':'')+'</td><td><p>'+esc(item.detail)+'</p>'+disclosure+'</td></tr>';
      }).join('');
      q('#web-'+scope+'-items').innerHTML=rows?'<table class="th-object-table th-env-table th-space"><thead><tr><th>检查项</th><th>状态</th><th>说明</th></tr></thead><tbody>'+rows+'</tbody></table>':'';
        environmentRowsSignature[scope]=signature;
      }
      q('#web-'+scope+'-log-panel').hidden=!job.log;q('#web-'+scope+'-log').textContent=job.log||'';
    }
  }
  async function followEnvironment(job){
    environmentJob=job;const id=job.id;renderEnvironment();
    while(environmentJob.status==='running'){
      await new Promise(resolve=>setTimeout(resolve,800));
      const latest=await api('/environment');
      if(!latest||latest.id!==id)throw new Error('操作已被其他页面更新，请重新检查');
      environmentJob=latest;renderEnvironment();
    }
    if(environmentJob.status!=='completed')throw new Error(environmentJob.message);
    return environmentJob;
  }
  async function runEnvironment(prepare,scope='environment'){
    environmentBusy=true;environmentScope=scope;environmentAction=prepare?'prepare':'check';environmentSelection[scope]=JSON.stringify(environmentBody());environmentButtons();
    q('#web-'+scope+'-status').textContent='正在提交操作，请稍候…';
    try{
      const job=await api('/environment/'+environmentAction,'POST',{...environmentBody(),scope});
      const result=await followEnvironment(job);
      if(!starting)feedback(result.message,result.ready?'success':'warning');
      return result;
    }finally{environmentBusy=false;environmentButtons();}
  }
  async function previewCleanup(scope){
    cleanupPlan=await api('/environment/cleanup-preview','POST',{...environmentBody(),scope});
    q('#web-cleanup-heading').textContent=(scope==='environment'?'清理依赖环境':'清理数据缓存')+' · 约 '+(cleanupPlan.bytes/1024/1024).toFixed(1)+' MB';
    q('#web-cleanup-items').innerHTML=cleanupPlan.items.length?cleanupPlan.items.map(i=>'<p>'+esc(i.label)+'<small class="th-default" style="display:block">'+esc(i.path)+'</small></p>').join(''):'<p>本次范围内没有可清理的内容。</p>';
    q('#web-cleanup-notes').textContent=cleanupPlan.notes.join(' ');
    q('#web-cleanup-confirm').hidden=false;environmentButtons();q('#web-cleanup-confirm').scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  async function cleanEnvironment(){
    if(!cleanupPlan)return;
    environmentBusy=true;environmentScope=cleanupPlan.scope;environmentAction='clean';environmentButtons();
    try{
      const job=await api('/environment/cleanup','POST',{plan_id:cleanupPlan.id});
      cleanupPlan=null;q('#web-cleanup-confirm').hidden=true;
      await followEnvironment(job);feedback('清理完成，测试对象和历史任务已保留');
    }finally{environmentBusy=false;environmentButtons();}
  }
  async function checkConnection(){
    const b=q('#web-check-connection'),body=connectionBody();
    b.disabled=true;connectionSelection=JSON.stringify(body);q('#web-connection-result').textContent='正在发送最小请求…';
    try{
      const result=await api('/connectivity','POST',body);
      const changed=connectionSelection!==JSON.stringify(connectionBody());
      q('#web-connection-result').textContent=changed?'配置已变更，请重新检查':(result.ready?'已通过 · ':'未通过 · ')+result.message+' · '+result.elapsed_ms+' ms';
      feedback(changed?'配置已变更，请重新检查连通性':result.ready?'连通性检查通过':'连通性检查未通过，请查看原因',!changed&&result.ready?'success':'warning');
    }catch(error){q('#web-connection-result').textContent='未完成 · '+error.message;throw error;}finally{b.disabled=false;}
  }
  async function checkJudgeConnection(){
    const body=judgeBody();judgeSelection=JSON.stringify(body);
    q('#web-judge-connection-result').textContent='正在向裁判模型发送最小请求…';
    try{
      const result=await api('/judge-connectivity','POST',body);
      const changed=judgeSelection!==JSON.stringify(judgeBody());
      q('#web-judge-connection-result').textContent=changed?'Judge 配置已变更，请重新检查':(result.ready?'已通过 · ':'未通过 · ')+result.message+' · '+result.elapsed_ms+' ms';
      feedback(changed?'Judge 配置已变更，请重新检查':result.ready?'Judge 连通性检查通过':'Judge 连通性检查未通过，请查看原因',!changed&&result.ready?'success':'warning');
    }catch(error){q('#web-judge-connection-result').textContent='未完成 · '+error.message;throw error;}
  }
  function invalidateChecks(){
    cleanupPlan=null;q('#web-cleanup-confirm').hidden=true;
    if(connectionSelection&&connectionSelection!==JSON.stringify(connectionBody()))q('#web-connection-result').textContent='对象、模型或协议已变更，请重新检查';
    if(judgeSelection&&judgeSelection!==JSON.stringify(judgeBody()))q('#web-judge-connection-result').textContent='Judge 模型、地址或密钥已变更，请重新检查';
    if(environmentJob)renderEnvironment();
  }
  async function replayTask(id){
    const task=await api('/tasks/'+id);state.replay=task;state.mode=task.spec.mode;state.datasets=task.spec.datasets;state.all=task.spec.all;state.connectionId=task.spec.object_id;
    for(const field of fields)q('#'+field).value=field==='th-model'?task.spec.model:field==='th-protocol'?task.spec.protocol:task.spec.options[field.slice(3)]||'';
    q('#th-perf-command').value=task.spec.options['perf-command']||'bench';q('#web-judge-key').value='';state.stage=2;render();
    q('#web-judge-key').placeholder='留空沿用原任务裁判密钥';
    invalidateChecks();feedback('原任务配置已带入，点击“开始测试”重新运行');
  }
  function renderTask(){
    const t=currentTask;if(!t)return;
    q('#web-task-title').textContent=t.spec.model+' · '+(t.spec.mode==='eval'?'效果评测':'性能压测');
    q('#web-task-meta').textContent=t.object.name+' · '+t.id.slice(0,8)+' · 创建于 '+displayDate(t.created)+(t.finished?' · 结束于 '+displayDate(t.finished):'');
    q('#web-task-status').textContent=statusNames[t.status]||t.status;q('#web-task-error').hidden=!t.error;q('#web-task-error').textContent=t.error||'';
    q('#web-task-progress').innerHTML=taskProgressMarkup(t);
    for(const tab of ['results','config','reports'])q('#web-task-'+tab).hidden=state.taskTab!==tab;
    qa('[data-task-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.taskTab===state.taskTab)));
    let results='';
    if(t.eval_results.length){results='<h3>数据集结果</h3><div class="th-table-wrap"><table><thead><tr><th>数据集</th><th>得分</th><th>有效样本</th><th>跳过</th><th>执行状态</th></tr></thead><tbody>'+t.eval_results.map(r=>'<tr><td>'+esc(r.dataset)+'</td><td>'+esc(r.score??'—')+'</td><td>'+esc(r.num_samples??'—')+'</td><td>'+esc(r.skipped_count??0)+'</td><td>'+(r.success?'完成':'失败')+'</td></tr>').join('')+'</tbody></table></div><p class="th-note">得分采用框架原始口径；执行完成不代表模型通过质量验收。</p>';}
    else if(t.perf_results.length){results='<h3>性能结果</h3><p class="th-note">以下指标来自框架统计；完整结论和并发梯度请查看“报告与日志”中的 HTML 报告。</p>'+t.perf_results.map((r,i)=>'<details class="th-advanced"'+(i===0?' open':'')+'><summary>压测结果 '+(i+1)+'</summary><div class="th-table-wrap"><table><tbody>'+Object.entries(r).map(([k,v])=>'<tr><th>'+esc(k)+'</th><td style="text-align:left">'+esc(typeof v==='object'?JSON.stringify(v):v)+'</td></tr>').join('')+'</tbody></table></div></details>').join('');}
    else results='<h3>测试结果</h3><p class="th-note">'+(['queued','running'].includes(t.status)?'任务尚未结束，结果生成后自动显示。':'尚无结果摘要，请查看运行日志。')+'</p>';
    q('#web-task-results').innerHTML=results;
    const view={测试对象:t.object.name,接口基础地址:t.object.url,最终请求地址:requestAddress(t.object.url,t.spec.protocol),模型名称:t.spec.model,接口协议:t.spec.protocol==='openai'?'OpenAI 兼容':'Anthropic 原生',测试类型:t.spec.mode==='eval'?'效果评测':'性能压测'};
    if(t.spec.mode==='eval')view.数据集=t.spec.datasets.map(id=>datasets.find(d=>d.id===id)?.name||id).join('、');
    const labels={'perf-command':'执行方式',bucket:'输入长度',number:'请求数',parallel:'并发数',buckets:'输入长度列表',ladder:'并发梯度',cooldown:'档间冷却（秒）',success:'成功率早停阈值（%）',warmup:'预热请求数',tokenizer:'Tokenizer',limit:'每数据集题数上限',repeats:'重复次数',batch:'同时评测题数','provider-name':'供应商名称','judge-model':'裁判模型','judge-url':'裁判基础地址',sandbox:'Sandbox 配置'};
    for(const [key,value] of Object.entries(t.spec.options))view[labels[key]||(key.startsWith('n-')?key.slice(2)+' 档请求数':key)]=value;
    q('#web-task-config').innerHTML='<h3>当次配置</h3><dl class="th-fields th-space">'+Object.entries(view).map(([label,value])=>'<div><dt class="th-small">'+esc(label)+'</dt><dd style="margin:5px 0 0">'+esc(value)+'</dd></div>').join('')+'</dl><p class="th-note">未填写的参数沿用框架默认值。此处记录运行时的配置，之后修改测试对象不会更改历史记录。</p><p class="th-note">框架版本：'+esc(t.framework_commit)+'</p>';
    q('#web-artifacts').innerHTML=t.artifacts.length?t.artifacts.map(a=>'<p class="th-note"><a target="_blank" rel="noopener noreferrer" href="/api/tasks/'+t.id+'/artifacts/'+a.path.split('/').map(encodeURIComponent).join('/')+'">'+esc(a.path)+'</a> · '+Math.ceil(a.size/1024)+' KB</p>').join(''):'<p class="th-note">报告尚未生成。</p>';
  }
  async function viewTask(id){state.taskId=id;state.stage=4;renderStages();q('#web-task-title').textContent='正在加载任务…';await refreshTask();}
  async function refreshTask(){
    if(loadingTask||!state.taskId)return;loadingTask=true;
    try{const id=state.taskId;const [task,log]=await Promise.all([api('/tasks/'+id),api('/tasks/'+id+'/logs')]);if(id===state.taskId){currentTask=task;renderTask();q('#web-task-log').textContent=log.text||'等待运行日志…';}}finally{loadingTask=false;}
  }
  root.addEventListener('click',async e=>{
    const b=e.target.closest('button');if(!b||b.disabled)return;q('#web-error').hidden=true;
    try{
      if(b.id==='web-logout'){await withButton(b,'正在退出…',async()=>{await api('/auth/logout','POST');leaveSession();});return;}
      if(b.id==='th-save-connection'){await withButton(b,'保存中…',saveConnection);return;}
      if(b.id==='th-new-object'){returnToTest=true;editConnection('');state.stage=1;render();return;}
      if(b.dataset.editObject){returnToTest=false;editConnection(b.dataset.editObject);q('#th-connection-name').focus();return;}
      if(b.id==='th-cancel-edit'){editConnection('');return;}
      if(b.dataset.deleteObject){pendingDeleteId=b.dataset.deleteObject;q('#th-delete-object-text').textContent='确定删除该测试对象？已有历史任务会保留。';q('#th-delete-object-confirm').hidden=false;return;}
      if(b.id==='th-cancel-delete-object'){pendingDeleteId='';q('#th-delete-object-confirm').hidden=true;return;}
      if(b.id==='th-confirm-delete-object'){await withButton(b,'删除中…',deleteConnection);return;}
      if(b.id==='web-start'){await startTask();return;}
      if(b.id==='web-view-active-task'){const task=activeTask();if(task)await viewTask(task.id);return;}
      if(b.id==='web-check-connection'){await withButton(b,'检查中…',checkConnection);return;}
      if(b.id==='web-check-judge-connection'){await withButton(b,'检查中…',checkJudgeConnection);return;}
      for(const scope of ['environment','data']){
        if(b.id==='web-check-'+scope){await runEnvironment(false,scope);return;}
        if(b.id==='web-prepare-'+scope){await runEnvironment(true,scope);return;}
        if(b.id==='web-clean-'+scope){await withButton(b,'正在计算清理范围…',()=>previewCleanup(scope));return;}
      }
      if(b.id==='web-confirm-cleanup'){await cleanEnvironment();return;}
      if(b.id==='web-cancel-cleanup'){cleanupPlan=null;q('#web-cleanup-confirm').hidden=true;return;}
      if(b.dataset.viewTask){await withButton(b,'加载中…',()=>viewTask(b.dataset.viewTask));return;}
      if(b.dataset.replayTask){await withButton(b,'载入中…',()=>replayTask(b.dataset.replayTask));return;}
      if(b.dataset.deleteTask){if(confirm('确定删除此任务及其报告？'))await withButton(b,'删除中…',async()=>{await api('/tasks/'+b.dataset.deleteTask,'DELETE');await loadTasks();feedback('任务及报告已删除');});return;}
      if(b.dataset.taskTab){state.taskTab=b.dataset.taskTab;renderTask();return;}
      if(b.dataset.stage){state.stage=Number(b.dataset.stage);returnToTest=false;render();if(state.stage===3)await withButton(b,'加载历史…',loadTasks);return;}
      if(b.dataset.mode){state.mode=b.dataset.mode;render();save();invalidateChecks();}
    }catch(error){showError(error);}
  });
  root.addEventListener('change',e=>{const el=e.target;
    if(el.id==='th-test-object'){if(el.value!=='replay'){state.replay=null;state.connectionId=el.value;}render();save();invalidateChecks();return;}
    if(el.id==='th-all-datasets'){state.all=el.checked;renderDatasets();}
    if(el.dataset.dataset){state.datasets=el.checked?[...new Set([...state.datasets,el.dataset.dataset])]:state.datasets.filter(id=>id!==el.dataset.dataset);}
    update();save();invalidateChecks();
  });
  root.addEventListener('input',e=>{if(fields.includes(e.target.id)||e.target.id==='web-judge-key'){update();invalidateChecks();}});
  async function init(){
    try{
      const saved=JSON.parse(localStorage.getItem('thvv-test-form')||'null');
      if(saved){state.mode=['perf','eval'].includes(saved.mode)?saved.mode:'perf';state.datasets=Array.isArray(saved.datasets)?saved.datasets:[];state.all=!!saved.all;state.connectionId=saved.connectionId||'';for(const id of fields)if(typeof saved.fields?.[id]==='string')q('#'+id).value=saved.fields[id];}
      render();
      const meta=await api('/meta');
      for(const [id,value] of meta.judge_defaults?[['th-judge-model',meta.judge_defaults.model],['th-judge-url',meta.judge_defaults.url]]:[]){
        q('#'+id).placeholder='框架默认：'+value;
        q('#'+id).parentElement.querySelector('.th-default').textContent='未配置时默认：'+value+'。';
      }
      if(!meta.engine.ready){q('#web-status').textContent='页面已就绪。可在下方分别检查、准备运行环境和数据。';q('#web-status').hidden=false;}
      await Promise.all([loadObjects(),loadTasks()]);
      environmentJob=await api('/environment');renderEnvironment();
    }catch(error){showError(error);}
  }
  setInterval(()=>{if(document.hidden||leavingSession)return;loadTasks().catch(showError);if(state.stage===4)refreshTask().catch(showError);if(!environmentBusy&&environmentJob?.status==='running')api('/environment').then(job=>{environmentJob=job;renderEnvironment();}).catch(showError);},2000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!leavingSession)loadTasks().catch(showError);});
  window.addEventListener('pageshow',event=>{if(event.persisted)window.location.reload();});
  init();
})();
