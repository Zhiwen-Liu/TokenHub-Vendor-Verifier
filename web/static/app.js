
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
    {id:'msr_vtt',name:'MSR-VTT',repeats:1,batch:5,multimodal:'video'},
    {id:'mvbench',name:'MVBench',repeats:1,batch:5,multimodal:'video'},
    {id:'tvbench',name:'TVBench',repeats:1,batch:5,multimodal:'video',min_evalscope:'1.9.1'},
    {id:'math_vision',name:'MathVision',repeats:1,batch:10,multimodal:'image'},
    {id:'hallusion_bench',name:'HallusionBench',repeats:1,batch:10,multimodal:'image'},
    {id:'videomme_v2',name:'Video-MME-v2',repeats:1,batch:5,multimodal:'video'}
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
    q('#th-datasets').innerHTML=datasets.map(d=>'<label class="th-dataset"><input type="checkbox" data-dataset="'+d.id+'" data-native="--datasets '+d.id+'"'+(chosen.has(d.id)?' checked':'')+(state.all?' disabled':'')+'><span>'+d.name+(d.multimodal==='video'?' 🎬':d.multimodal==='image'?' 🖼️':'')+'<small>默认 '+d.repeats+' 轮 · 并发 '+d.batch+(d.judge?' · Judge':'')+(d.multimodal?' · '+({video:'视频多模态',image:'图像多模态'}[d.multimodal]):'')+'</small></span></label>').join('');
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
    const mmVideo=chosen.filter(d=>d.multimodal==='video');
    const mmImage=chosen.filter(d=>d.multimodal==='image');
    if(mmVideo.length)notes.push('⚠️ '+mmVideo.map(d=>d.name).join('、')+' 为视频多模态数据集，需要模型支持视频输入，纯文本模型不适用。');
    if(mmImage.length)notes.push('⚠️ '+mmImage.map(d=>d.name).join('、')+' 为图像多模态数据集，需要模型支持图像输入，纯文本模型不适用。');
    const upgradeNeeded=chosen.filter(d=>d.min_evalscope);
    if(upgradeNeeded.length){
      const versions=[...new Set(upgradeNeeded.map(d=>d.min_evalscope))];
      notes.push('⚠️ '+upgradeNeeded.map(d=>d.name).join('、')+' 需要 evalscope >= '+versions.join(' / ')+'（当前锁定 1.9.0）。在运行环境中"准备缺失项"时会检测并提示升级。');
    }
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
  const statusNames={queued:'等待运行',running:'运行中',paused:'已暂停',succeeded:'执行完成',failed:'执行失败',interrupted:'已中断'};
  let tasks=[],currentTask=null,loadingTask=false,tasksRequest=null,tasksKnown=false;
  let baseline=null,baselineRequest=null;
  function loadBaseline(){
    if(baseline)return Promise.resolve(baseline);
    if(baselineRequest)return baselineRequest;
    baselineRequest=api('/baselines/lookup').then(d=>{baseline=d;return d;}).catch(()=>{baselineRequest=null;return null;});
    return baselineRequest;
  }
  let baselineDB=null;
  let perfMetricsList=['TTFT (ms)','TPOT (ms)','ITL (ms)','Output Throughput (tok/s)','Total Throughput (tok/s)','Req Throughput (req/s)','Avg Latency (s)'];
  let environmentJob=null,environmentBusy=false,starting=false,environmentSelection={},connectionSelection='',judgeSelection='';
  let environmentFlowScopes=new Set();
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
    const hidden=q('#th-test-object');
    const btn=q('#th-test-object-btn');
    const menu=q('#th-test-object-menu');
    let items=state.connections.map(c=>({value:c.id,label:c.name,sub:c.url+(c.has_api_key?' · 已保存密钥':'')}));
    if(state.replay)items.unshift({value:'replay',label:state.replay.object.name+'（原任务配置）',sub:state.replay.object.url});
    const emptyLabel=state.connections.length?'请选择测试对象':'暂无测试对象';
    items.unshift({value:'',label:emptyLabel,sub:''});
    const current=state.replay?'replay':state.connectionId;
    hidden.value=current;
    const sel=items.find(i=>i.value===current)||items[0];
    btn.textContent=sel.value?sel.label:emptyLabel;
    if(sel.value)btn.removeAttribute('data-placeholder');else btn.setAttribute('data-placeholder','');
    menu.innerHTML=items.map(i=>'<button type="button" class="th-dropdown-item" data-dropdown-value="'+esc(i.value)+'" aria-selected="'+(i.value===current)+'">'+esc(i.label)+(i.sub?'<span class="th-small">'+esc(i.sub)+'</span>':'')+'</button>').join('');
    q('#th-object-rows').innerHTML=state.connections.length?state.connections.map(c=>'<tr><td>'+esc(c.name)+(c.has_api_key?'<small class="th-note"> · 已保存密钥</small>':'')+'</td><td>'+esc(c.url)+'</td><td><div class="th-task-actions"><button type="button" class="th-task-action" data-edit-object="'+esc(c.id)+'">编辑</button><button type="button" class="th-task-action th-task-delete" data-delete-object="'+esc(c.id)+'">删除</button></div></td></tr>').join(''):'<tr><td colspan="3">暂无测试对象，填写上方信息后保存。</td></tr>';
    q('#th-connection-heading').textContent=editingId?'编辑测试对象':'新增测试对象';
    buttonText(q('#th-save-connection'),returnToTest?'保存并返回测试配置':editingId?'保存修改':'保存测试对象');
    q('#th-cancel-edit').hidden=!editingId;
    q('#web-clear-key-row').hidden=!state.connections.find(c=>c.id===editingId)?.has_api_key;
  }
  // Custom dropdown toggle
  function setDropdown(id,value){
    const hidden=q('#'+id);if(!hidden)return;
    hidden.value=value;
    const wrap=q('#'+id+'-wrap');if(!wrap)return;
    const btn=q('#'+id+'-btn');
    const items=wrap.querySelectorAll('.th-dropdown-item');
    let label='';
    items.forEach(item=>{
      const selected=item.dataset.dropdownValue===value;
      item.setAttribute('aria-selected',selected);
      if(selected)label=item.textContent.split('\n')[0].trim();
    });
    if(label&&value){btn.textContent=label;btn.removeAttribute('data-placeholder');}
    else{const first=items[0];btn.textContent=first?first.textContent.split('\n')[0].trim():'';btn.setAttribute('data-placeholder','');}
  }
  function syncAllDropdowns(){['th-protocol','th-perf-command','th-bucket'].forEach(id=>setDropdown(id,q('#'+id).value));}
  root.addEventListener('click',e=>{
    const btn=e.target.closest('.th-dropdown-btn');
    if(btn){const menu=btn.nextElementSibling;qa('.th-dropdown-menu').forEach(m=>{if(m!==menu)m.hidden=true;});menu.hidden=!menu.hidden;if(!menu.hidden)btn.focus();e.stopPropagation();return;}
    const item=e.target.closest('.th-dropdown-item');
    if(item){const val=item.dataset.dropdownValue;const wrap=item.closest('.th-dropdown');const menu=wrap.querySelector('.th-dropdown-menu');const hidden=wrap.querySelector('input[type=hidden]');menu.hidden=true;
      if(wrap.id==='th-test-object-wrap'){if(val!=='replay'){state.replay=null;state.connectionId=val;}render();save();invalidateChecks();}
      else if(hidden){hidden.value=val;setDropdown(hidden.id,val);update();save();invalidateChecks();}
      return;}
    qa('.th-dropdown-menu').forEach(m=>m.hidden=true);
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape')qa('.th-dropdown-menu').forEach(m=>m.hidden=true);});
  function renderStages(){
    const obj=state.replay?.object||state.connections.find(c=>c.id===state.connectionId);
    qa('[data-stage-panel]').forEach(p=>p.hidden=Number(p.dataset.stagePanel)!==state.stage);
    qa('.th-stages button').forEach(b=>{const s=Number(b.dataset.stage);if(s===(state.stage===4?3:state.stage))b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
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
      tasks=await api('/tasks');q('#web-history-rows').innerHTML=tasks.length?tasks.map(t=>{
        const dsNames=t.spec.mode==='eval'?t.spec.datasets.map(id=>{const d=datasets.find(d=>d.id===id);return d?d.name:id;}):[t.spec.options?.['perf-command']==='bench-all'?'全档位压测':(t.spec.options?.bucket||'')+'档压测'];
        const dsTags=dsNames.map(n=>'<span class="th-dataset-tag">'+esc(n)+'</span>').join('');
        const typeName=t.spec.mode==='perf'?'性能压测':'效果评测';
        const displayStatus=t.paused?'已暂停':(statusNames[t.status]||t.status);
        const statusKey=t.paused?'paused':t.status;
        const statusBadge='<span class="th-task-badge" data-status="'+esc(statusKey)+'">'+esc(displayStatus)+'</span>';
        const progress=t.progress?taskProgressMarkup(t,true):'';
        const isActive=['queued','running'].includes(t.status);
        const pauseBtn=t.status==='running'&&!t.paused?'<button type="button" class="th-task-action" data-pause-task="'+t.id+'">暂停</button>':'';
        const resumeBtn=t.paused?'<button type="button" class="th-task-action" data-resume-task="'+t.id+'">恢复</button>':'';
        const continueBtn=t.can_continue?'<button type="button" class="th-task-action" data-continue-task="'+t.id+'">继续</button>':'';
        const retryFailedBtn=t.can_retry_failed?'<button type="button" class="th-task-action" data-retry-failed="'+t.id+'">补跑失败</button>':'';
        const importBtn=t.status==='succeeded'?'<button type="button" class="th-task-action" data-import-task="'+t.id+'">刷入基线</button>':'';
        const stopBtn=isActive?'<button type="button" class="th-task-action th-task-delete" data-stop-task="'+t.id+'">终止</button>':'';
        const deleteBtn='<button type="button" class="th-task-action th-task-delete" data-delete-task="'+t.id+'"'+(isActive?' disabled':'')+'>删除</button>';
        return '<tr><td><strong>'+esc(t.object.name)+'</strong><small class="th-note" style="display:block">'+esc(t.spec.model)+' · '+esc(t.id.slice(0,8))+'</small></td><td><span class="th-small">'+esc(typeName)+'</span><div class="th-dataset-tags">'+dsTags+'</div></td><td>'+statusBadge+progress+'</td><td><small class="th-note">'+esc(displayDate(t.created))+(t.finished?'<br>'+esc(displayDate(t.finished)):'')+'</small></td><td><div class="th-task-actions"><button type="button" class="th-task-action" data-view-task="'+t.id+'">查看</button><button type="button" class="th-task-action" data-replay-task="'+t.id+'">重放</button>'+continueBtn+retryFailedBtn+importBtn+pauseBtn+resumeBtn+stopBtn+deleteBtn+'</div></td></tr>';
      }).join(''):'<tr><td colspan="5">暂无测试任务。</td></tr>';
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
      buttonBusy(b,true,'正在检查就绪状态…');
      const ready=await api('/environment/readiness','POST',environmentBody());
      if(!ready.environment||!ready.data){
        environmentFlowScopes=new Set();
        for(const scope of ['environment','data']){
          if(ready[scope])continue;
          buttonBusy(b,true,scope==='environment'?'正在检查环境…':'正在检查数据…');
          const result=await runEnvironment(false,scope,true);
          environmentFlowScopes.add(scope);
          if(!result.ready)throw new Error(scope==='environment'?'运行环境尚未就绪，可在运行环境中点击"准备缺失项"':'数据尚未就绪，可在数据分组中点击"准备缺失项"');
        }
        if(JSON.stringify(body)!==JSON.stringify(taskBody())||replayId!==state.replay?.id)throw new Error('配置已变更，请按新配置重新开始');
      }
      buttonBusy(b,true,'正在创建任务…');
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
      const stopBtn=q('#web-stop-'+scope);
      if(stopBtn){stopBtn.hidden=!(busy&&activeScope===scope);stopBtn.disabled=false;}
    }
    q('#web-start').disabled=busy||starting||!tasksKnown||!!activeTask();
    if(!starting)buttonText(q('#web-start'),!tasksKnown?'正在确认任务状态…':activeTask()?'等待当前任务结束':'开始测试');
    q('#web-confirm-cleanup').disabled=busy||starting||!cleanupPlan?.items.length;
  }
  function renderEnvironment(){
    environmentButtons();
    const names={checking:'检查中',preparing:'准备中',ready:'已就绪',online:'仍需联网',missing:'未就绪',blocked:'等待依赖',manual:'需手动准备',skipped:'已跳过'};
    for(const scope of ['environment','data']){
      const job=environmentJob?.groups?.[scope];
      const running=environmentJob?.status==='running';
      const activeScope=environmentJob?.scope;
      const lastScope=environmentScope||activeScope;
      const hideOther=activeScope&&activeScope!==scope&&!environmentFlowScopes.has(scope);
      if(!job||hideOther){q('#web-'+scope+'-status').textContent='尚未检查';q('#web-'+scope+'-items').innerHTML='';q('#web-'+scope+'-log-panel').hidden=true;const pEl=q('#web-'+scope+'-progress');if(pEl)pEl.hidden=true;delete environmentRowsSignature[scope];continue;}
      const changed=scope==='data'&&environmentSelection[scope]&&environmentSelection[scope]!==JSON.stringify(environmentBody());
      const seconds=Math.max(0,(job.finished||Date.now()/1000)-(job.started||Date.now()/1000));
      const items=job.items||[];
      const done=items.filter(i=>['ready','online','missing','blocked','manual','skipped'].includes(i.state)).length;
      const total=items.length;
      const isRunning=job.status==='running';
      let statusLine=(changed?'配置已变更，以下为上次选择的检查结果。':'')+job.message+' · '+(job.finished?'用时 '+seconds.toFixed(1)+' 秒':'已用 '+Math.floor(seconds)+' 秒');
      if(isRunning&&total>0)statusLine+=' · '+done+'/'+total+' 项';
      q('#web-'+scope+'-status').textContent=statusLine;
      const progressEl=q('#web-'+scope+'-progress');
      if(progressEl){
        progressEl.hidden=!isRunning||total===0;
        if(isRunning&&total>0){progressEl.max=total;progressEl.value=done;}
      }
      if(scope==='environment'&&job.ready)q('#web-status').hidden=true;
      const signature=JSON.stringify(job.items);
      if(signature!==environmentRowsSignature[scope]){
      const expanded=new Set(qa('[data-env-detail]').filter(el=>el.open).map(el=>el.dataset.envDetail));
      const allItems=job.items||[];
      const childMap={};
      allItems.forEach(item=>{if(item.parent){(childMap[item.parent]=childMap[item.parent]||[]).push(item);}});
      const topItems=allItems.filter(item=>!item.parent);
      function renderItem(item,indent){
        const versions=(item.versions||[]).map(v=>'<tr><td>'+esc(v.name)+'</td><td>'+esc(v.required)+(v.install_constraint&&v.install_constraint!==v.required?'<small class="th-default" style="display:block">安装约束：'+esc(v.install_constraint)+'</small>':'')+'</td><td>'+esc(v.installed||'未安装')+(v.source_revision?'<small class="th-default" style="display:block">来源：'+esc(v.source_revision)+'</small>':'')+'</td><td>'+esc(v.matches?'符合':'不符合')+'</td></tr>').join('');
        let disclosure='';
        if(versions||item.error_details){
          disclosure='<details class="th-env-details" data-env-detail="'+esc(item.id)+'"'+(expanded.has(item.id)?' open':'')+'><summary>'+(versions?'查看版本信息':'查看错误详情')+'</summary>';
          if(versions)disclosure+='<div class="th-table-wrap"><table class="th-env-versions"><thead><tr><th>组件</th><th>框架要求</th><th>本机版本</th><th>结果</th></tr></thead><tbody>'+versions+'</tbody></table></div><p class="th-note">'+(item.id==='java'?'Java 8 来自框架使用的 pycocoevalcap 评分组件要求；自动准备使用 Azul 官方 Java 8 运行环境。':item.id==='docker'?'框架未锁定 Docker 版本；检查当前服务是否可用，安装时使用对应 Linux 发行版的 Docker 官方稳定源。':'按本地框架 6e7a150 的依赖文件核对。安装约束若不同，会另外列出。')+'</p>';
          if(item.error_details)disclosure+='<pre>'+esc(item.error_details)+'</pre>';
          disclosure+='</details>';
        }
        const disposition={reused:job.action==='check'?'可直接复用':'复用已有',prepared:'本次准备'}[item.disposition];
        const canSkip=['checking','preparing'].includes(item.state);
        const skipBtn=canSkip?'<button type="button" class="th-task-action th-task-delete" data-skip-item="'+esc(item.id)+'" style="margin-top:6px;font-size:12px">跳过</button>':'';
        const labelStyle=indent?'style="padding-left:24px;font-size:12px;color:var(--th-muted)"':'';
        const dispText=disposition?'<small class="th-default" style="display:block">'+disposition+'</small>':'';
        return '<tr><td '+labelStyle+'>'+(indent?'└ ':'')+esc(item.label)+'</td><td><span class="th-env-state" data-env-state="'+esc(item.state)+'">'+esc(names[item.state]||item.state)+'</span></td><td><p>'+esc(item.detail)+'</p>'+dispText+disclosure+skipBtn+'</td></tr>';
      }
      const dockerDatasets=new Set(datasets.filter(d=>d.docker||d.sandbox).map(d=>d.id));
      const rows=topItems.map(item=>{
        let html=renderItem(item,false);
        const children=childMap[item.id]||[];
        if(item.state==='ready'&&children.length===0&&item.id.startsWith('data:')&&dockerDatasets.has(item.id.slice(5))){
          html+='<tr><td style="padding-left:24px;font-size:12px;color:var(--th-muted)">└ Docker 镜像</td><td><span class="th-env-state" data-env-state="checking">待检查</span></td><td><p class="th-small" style="color:var(--th-muted)">镜像检查将在所有数据集完成后进行</p></td></tr>';
        }
        children.forEach(child=>{html+=renderItem(child,true);});
        return html;
      }).join('');
      q('#web-'+scope+'-items').innerHTML=rows?'<table class="th-object-table th-env-table th-space"><thead><tr><th>检查项</th><th>状态</th><th>说明</th></tr></thead><tbody>'+rows+'</tbody></table>':'';
        environmentRowsSignature[scope]=signature;
      }
      q('#web-'+scope+'-log-panel').hidden=!job.log;q('#web-'+scope+'-log').textContent=job.log||'';
      if(job.log&&job.status==='running'){const el=q('#web-'+scope+'-log');el.scrollTop=el.scrollHeight;}
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
  async function runEnvironment(prepare,scope='environment',fromFlow=false){
    if(!fromFlow)environmentFlowScopes=new Set();
    environmentBusy=true;environmentScope=scope;environmentAction=prepare?'prepare':'check';environmentSelection[scope]=JSON.stringify(environmentBody());environmentButtons();
    q('#web-'+scope+'-status').textContent='正在提交操作，请稍候…';
    try{
      const job=await api('/environment/'+environmentAction,'POST',{...environmentBody(),scope});
      const result=await followEnvironment(job);
      if(!starting)feedback(result.message,result.ready?'success':'warning');
      return result;
    }finally{environmentBusy=false;environmentButtons();}
  }
  async function stopEnvironment(){
    try{
      const result=await api('/environment/stop','POST');
      environmentJob=result;environmentBusy=false;renderEnvironment();environmentButtons();
      feedback('操作已终止','warning');
    }catch(error){feedback(error.message,'error');}
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
    environmentFlowScopes=new Set();
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
    environmentFlowScopes=new Set();
    if(connectionSelection&&connectionSelection!==JSON.stringify(connectionBody()))q('#web-connection-result').textContent='对象、模型或协议已变更，请重新检查';
    if(judgeSelection&&judgeSelection!==JSON.stringify(judgeBody()))q('#web-judge-connection-result').textContent='Judge 模型、地址或密钥已变更，请重新检查';
    if(environmentJob)renderEnvironment();
  }
  async function replayTask(id){
    const task=await api('/tasks/'+id);state.replay=task;state.mode=task.spec.mode;state.datasets=task.spec.datasets;state.all=task.spec.all;state.connectionId=task.spec.object_id;
    for(const field of fields)q('#'+field).value=field==='th-model'?task.spec.model:field==='th-protocol'?task.spec.protocol:task.spec.options[field.slice(3)]||'';
    q('#th-perf-command').value=task.spec.options['perf-command']||'bench';q('#web-judge-key').value='';state.stage=2;syncAllDropdowns();render();
    q('#web-judge-key').placeholder='留空沿用原任务裁判密钥';
    invalidateChecks();feedback('原任务配置已带入，点击“开始测试”重新运行');
  }
  function renderTask(){
    const t=currentTask;if(!t)return;
    q('#web-task-title').textContent=t.object.name+' · '+t.spec.model+' · '+(t.spec.mode==='eval'?'效果评测':'性能压测');
    q('#web-task-meta').textContent=t.object.name+' · '+t.id.slice(0,8)+' · 创建于 '+displayDate(t.created)+(t.finished?' · 结束于 '+displayDate(t.finished):'');
    q('#web-task-status').textContent=t.paused?'已暂停':(statusNames[t.status]||t.status);q('#web-task-error').hidden=!t.error;q('#web-task-error').textContent=t.error||'';
    const stopEl=q('#web-task-stop');if(stopEl)stopEl.remove();
    const pauseEl=q('#web-task-pause');if(pauseEl)pauseEl.remove();
    const contEl=q('#web-task-continue');if(contEl)contEl.remove();
    const retryEl=q('#web-task-retry-failed');if(retryEl)retryEl.remove();
    const container=q('#web-task-status').parentElement;
    if(['queued','running'].includes(t.status)){
      if(t.status==='running'&&!t.paused){const pb=document.createElement('button');pb.type='button';pb.className='th-link-button';pb.id='web-task-pause';pb.dataset.pauseTask=t.id;pb.textContent='暂停';pb.style.marginLeft='12px';container.appendChild(pb);}
      if(t.paused){const rb=document.createElement('button');rb.type='button';rb.className='th-link-button';rb.id='web-task-pause';rb.dataset.resumeTask=t.id;rb.textContent='恢复';rb.style.marginLeft='12px';container.appendChild(rb);}
      const sb=document.createElement('button');sb.type='button';sb.className='th-link-button th-task-delete';sb.id='web-task-stop';sb.dataset.stopTask=t.id;sb.textContent='终止';sb.style.marginLeft='8px';container.appendChild(sb);
    }
    if(t.can_continue){const cb=document.createElement('button');cb.type='button';cb.className='th-link-button';cb.id='web-task-continue';cb.dataset.continueTask=t.id;cb.textContent='继续执行';cb.style.marginLeft='12px';container.appendChild(cb);}
    if(t.can_retry_failed){const rb=document.createElement('button');rb.type='button';rb.className='th-link-button';rb.id='web-task-retry-failed';rb.dataset.retryFailed=t.id;rb.textContent='补跑失败样本';rb.style.marginLeft='8px';container.appendChild(rb);}
    q('#web-task-progress').innerHTML=taskProgressMarkup(t);
    for(const tab of ['results','config','reports'])q('#web-task-'+tab).hidden=state.taskTab!==tab;
    qa('[data-task-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.taskTab===state.taskTab)));
    let results='';
    if(t.eval_results.length){
      const blRow=id=>baseline?baseline.rows.find(b=>b.dataset_id===id):null;
      results='<h3>数据集结果</h3><div class="th-table-wrap"><table style="table-layout:auto"><thead><tr><th>数据集</th><th style="text-align:center">得分</th><th style="text-align:center">基线范围</th><th style="text-align:center">样本</th><th style="text-align:center">跳过</th><th style="text-align:center">状态</th></tr></thead><tbody>'+t.eval_results.map(r=>{
        const dName=(datasets.find(d=>d.id===r.dataset)||{}).name||r.dataset;
        const b=blRow(r.dataset);
        let rawScore=r.score!=null?Number(r.score):null;
        let sc=rawScore;
        if(sc!=null&&!Number.isNaN(sc)&&sc>=0&&sc<=1)sc=sc*100;
        let scoreCell=sc!=null?'<strong>'+sc.toFixed(2)+'</strong>':'—';
        let baseCell='<span class="th-small" style="color:var(--th-muted)">—</span>';
        if(b){
          const vals=Object.values(b.scores);
          const mn=Math.min(...vals),mx=Math.max(...vals);
          if(sc!=null){
            const modelKey=(t.spec.model||'').toLowerCase().trim();
            const exact=Object.keys(b.scores).find(k=>k.toLowerCase().trim()===modelKey);
            const sorted=[...vals].sort((a,c)=>a-c);
            const median=sorted[Math.floor(sorted.length/2)];
            const refName=exact||'中位';
            const ref=exact?b.scores[exact]:median;
            const gap=sc-ref;
            const ok=sc>=ref*0.96;
            const verdict=Math.abs(gap)<=ref*0.04?'容差内':(gap>=0?'+'+gap.toFixed(1):gap.toFixed(1));
            scoreCell='<strong style="color:'+(ok?'#28603b':'#b32a36')+'">'+sc.toFixed(2)+'</strong><div style="font-size:11px;color:'+(ok?'#28603b':'#b32a36')+';margin-top:2px">'+(ok?'✔ ':'✘ ')+'vs '+esc(refName)+' '+esc(ref)+' ('+verdict+')</div>';
          }
          const detailRows=Object.entries(b.scores).map(([m,v])=>'<tr><td style="text-align:left;padding:3px 8px;font-size:12px">'+esc(m)+'</td><td style="text-align:right;padding:3px 8px;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums">'+esc(v)+'</td></tr>').join('');
          baseCell='<div style="font-weight:600">'+mn+' ~ '+mx+'</div><details style="margin-top:4px;font-size:11px"><summary style="cursor:pointer;color:var(--th-accent)">'+Object.keys(b.scores).length+' 个模型基线 '+esc(b.tolerance)+'</summary><table style="margin-top:4px;width:100%"><tbody>'+detailRows+'</tbody></table></details>';
        }
        return '<tr><td>'+esc(dName)+'</td><td style="text-align:center">'+scoreCell+'</td><td style="text-align:center;min-width:120px">'+baseCell+'</td><td style="text-align:center">'+esc(r.num_samples??'—')+'</td><td style="text-align:center">'+esc(r.skipped_count??0)+'</td><td style="text-align:center">'+(r.success?'完成':'<span style="color:#b32a36">失败</span>')+'</td></tr>';
      }).join('')+'</tbody></table></div><p class="th-note">基线来自基线管理数据库，各模型精度可在 ±2-4% 上下浮动；展开可查看各模型基线分数。</p>';
      if(t.status==='succeeded')results+='<div style="margin-top:12px"><button type="button" class="th-link-button" data-import-task="'+t.id+'">将本次结果刷入基线</button></div>';
    }
    else if(t.perf_results.length){
      results='<h3>性能结果</h3>';
      t.perf_results.forEach((r,i)=>{
        const get=(k,d)=>r[k]!==undefined?r[k]:d;
        const fmt=(v,u)=>v===undefined||v===null?'—':typeof v==='number'?(Number.isInteger(v)?v.toLocaleString():v.toFixed(2))+' '+u:String(v);
        const successRate=get('Total Requests',0)>0?Math.round(100*get('Success Requests',0)/get('Total Requests',1)):0;
        const srColor=successRate>=99?'#28a745':successRate>=90?'#e8a630':'#d64545';
        results+='<div class="th-panel" style="margin-top:12px"><h3>压测 '+(i+1)+(get('Concurrency')?' · 并发 '+get('Concurrency'):'')+'</h3>';
        results+='<div class="th-monitor-cards" style="margin:12px 0">';
        results+='<div class="th-monitor-card"><div class="th-card-label">成功率</div><div class="th-card-value" style="color:'+srColor+'">'+successRate+'%</div><div class="th-card-label">'+get('Success Requests',0)+' / '+get('Total Requests',0)+'</div></div>';
        results+='<div class="th-monitor-card"><div class="th-card-label">TTFT</div><div class="th-card-value" style="color:var(--th-accent)">'+fmt(get('TTFT (ms)'),'ms')+'</div><div class="th-card-label">首 Token 延迟</div></div>';
        results+='<div class="th-monitor-card"><div class="th-card-label">TPOT</div><div class="th-card-value" style="color:var(--th-accent)">'+fmt(get('TPOT (ms)'),'ms')+'</div><div class="th-card-label">每 Token 延迟</div></div>';
        results+='<div class="th-monitor-card"><div class="th-card-label">吞吐</div><div class="th-card-value" style="color:var(--th-accent)">'+fmt(get('Output Throughput (tok/s)'),'tok/s')+'</div><div class="th-card-label">输出吞吐</div></div>';
        results+='</div>';
        const groups=[
          {title:'延迟指标',keys:['Avg Latency (s)','TTFT (ms)','First-Turn TTFT (ms)','TPOT (ms)','ITL (ms)']},
          {title:'吞吐指标',keys:['Req Throughput (req/s)','Output Throughput (tok/s)','Input Throughput (tok/s)','Total Throughput (tok/s)']},
          {title:'Token 统计',keys:['Avg Input Tokens','Avg Output Tokens','Decoded Tok/Iter','Spec. Accept Rate']},
          {title:'请求统计',keys:['Total Requests','Success Requests','Failed Requests','Concurrency','Request Rate (req/s)','Test Duration (s)','Avg Turns/Request','KV Cache Hit Rate (%)']},
        ];
        results+='<div class="th-monitor-grid" style="margin-top:8px">';
        groups.forEach(g=>{
          const items=g.keys.filter(k=>r[k]!==undefined);
          if(!items.length)return;
          results+='<div style="padding:12px;background:var(--th-bg);border-radius:8px"><div style="font-size:13px;font-weight:600;margin-bottom:8px">'+g.title+'</div>';
          items.forEach(k=>{
            const v=r[k];const display=typeof v==='number'?(Number.isInteger(v)?v.toLocaleString():v.toFixed(4)):String(v);
            results+='<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid var(--th-line)"><span style="color:var(--th-muted)">'+esc(k)+'</span><span style="font-variant-numeric:tabular-nums;font-weight:500">'+esc(display)+'</span></div>';
          });
          results+='</div>';
        });
        results+='</div></div>';
      });
      results+='<p class="th-note" style="margin-top:12px">完整结论和并发梯度请查看"报告与日志"中的 HTML 报告。</p>';
      if(t.status==='succeeded')results+='<div style="margin-top:12px"><button type="button" class="th-link-button" data-import-task="'+t.id+'">将本次结果刷入基线</button></div>';
    }
    else results='<h3>测试结果</h3><p class="th-note">'+(['queued','running'].includes(t.status)?'任务尚未结束，结果生成后自动显示。':'尚无结果摘要，请查看运行日志。')+'</p>';
    q('#web-task-results').innerHTML=results;
    const view={测试对象:t.object.name,接口基础地址:t.object.url,最终请求地址:requestAddress(t.object.url,t.spec.protocol),模型名称:t.spec.model,接口协议:t.spec.protocol==='openai'?'OpenAI 兼容':'Anthropic 原生',测试类型:t.spec.mode==='eval'?'效果评测':'性能压测'};
    if(t.spec.mode==='eval')view.数据集=t.spec.datasets.map(id=>datasets.find(d=>d.id===id)?.name||id).join('、');
    const labels={'perf-command':'执行方式',bucket:'输入长度',number:'请求数',parallel:'并发数',buckets:'输入长度列表',ladder:'并发梯度',cooldown:'档间冷却（秒）',success:'成功率早停阈值（%）',warmup:'预热请求数',tokenizer:'Tokenizer',limit:'每数据集题数上限',repeats:'重复次数',batch:'同时评测题数','provider-name':'供应商名称','judge-model':'裁判模型','judge-url':'裁判基础地址',sandbox:'Sandbox 配置'};
    for(const [key,value] of Object.entries(t.spec.options))view[labels[key]||(key.startsWith('n-')?key.slice(2)+' 档请求数':key)]=value;
    q('#web-task-config').innerHTML='<h3>当次配置</h3><dl class="th-fields th-space">'+Object.entries(view).map(([label,value])=>'<div><dt class="th-small">'+esc(label)+'</dt><dd style="margin:5px 0 0">'+esc(value)+'</dd></div>').join('')+'</dl><p class="th-note">未填写的参数沿用框架默认值。此处记录运行时的配置，之后修改测试对象不会更改历史记录。</p><p class="th-note">框架版本：'+esc(t.framework_commit)+'</p>';
    q('#web-artifacts').innerHTML=t.artifacts.length?t.artifacts.map(a=>'<p class="th-note"><a target="_blank" rel="noopener noreferrer" href="/api/tasks/'+t.id+'/artifacts/'+a.path.split('/').map(encodeURIComponent).join('/')+'">'+esc(a.path)+'</a> · '+Math.ceil(a.size/1024)+' KB</p>').join(''):'<p class="th-note">报告尚未生成。</p>';
  }
  async function viewTask(id){state.taskId=id;state.stage=4;lastTaskRenderStatus=null;renderStages();q('#web-task-title').textContent='正在加载任务…';await refreshTask();}
  let lastTaskRenderStatus=null;
  async function refreshTask(){
    if(loadingTask||!state.taskId)return;loadingTask=true;
    try{const id=state.taskId;const [task,log]=await Promise.all([api('/tasks/'+id),api('/tasks/'+id+'/logs'),loadBaseline()]);if(id===state.taskId){
      const statusChanged=!currentTask||currentTask.status!==task.status||lastTaskRenderStatus!==task.status;
      currentTask=task;
      if(statusChanged||['queued','running'].includes(task.status)){renderTask();lastTaskRenderStatus=task.status;}
      q('#web-task-log').textContent=log.text||'等待运行日志…';
      if(['queued','running'].includes(task.status)){const el=q('#web-task-log');el.scrollTop=el.scrollHeight;}
    }}finally{loadingTask=false;}
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
        if(b.id==='web-stop-'+scope){await stopEnvironment();return;}
        if(b.id==='web-clean-'+scope){await withButton(b,'正在计算清理范围…',()=>previewCleanup(scope));return;}
      }
      if(b.id==='web-confirm-cleanup'){await cleanEnvironment();return;}
      if(b.id==='web-cancel-cleanup'){cleanupPlan=null;q('#web-cleanup-confirm').hidden=true;return;}
      if(b.id==='web-select-all-datasets'){state.all=false;state.datasets=datasets.map(d=>d.id);renderDatasets();update();save();invalidateChecks();return;}
      if(b.id==='web-deselect-all-datasets'){state.all=false;state.datasets=[];renderDatasets();update();save();invalidateChecks();return;}
      if(b.dataset.viewTask){await withButton(b,'加载中…',()=>viewTask(b.dataset.viewTask));return;}
      if(b.dataset.skipItem){await api('/environment/stop-item','POST',{item_id:b.dataset.skipItem});return;}
      if(b.dataset.replayTask){await withButton(b,'载入中…',()=>replayTask(b.dataset.replayTask));return;}
      if(b.dataset.deleteTask){if(confirm('确定删除此任务及其报告？'))await withButton(b,'删除中…',async()=>{await api('/tasks/'+b.dataset.deleteTask,'DELETE');await loadTasks();feedback('任务及报告已删除');});return;}
      if(b.dataset.stopTask){if(confirm('确定终止此任务？'))await withButton(b,'终止中…',async()=>{await api('/tasks/'+b.dataset.stopTask+'/stop','POST');await loadTasks();feedback('任务已终止','warning');});return;}
      if(b.dataset.pauseTask){await withButton(b,'暂停中…',async()=>{await api('/tasks/'+b.dataset.pauseTask+'/pause','POST');await loadTasks();feedback('任务已暂停');});return;}
      if(b.dataset.resumeTask){await withButton(b,'恢复中…',async()=>{await api('/tasks/'+b.dataset.resumeTask+'/resume','POST');await loadTasks();feedback('任务已恢复');});return;}
      if(b.dataset.continueTask){await withButton(b,'续跑中…',async()=>{await api('/tasks/'+b.dataset.continueTask+'/continue','POST');await loadTasks();feedback('任务已恢复执行');});return;}
      if(b.dataset.retryFailed){await withButton(b,'创建中…',async()=>{await api('/tasks/'+b.dataset.retryFailed+'/retry-failed','POST');await loadTasks();feedback('已创建补跑任务，将只重跑失败样本，已成功的直接复用');});return;}
      if(b.dataset.importTask){await withButton(b,'刷入中…',async()=>{const res=await api('/baselines/import','POST',{task_id:b.dataset.importTask});baseline=null;baselineRequest=null;feedback('已导入 '+res.imported+' 条效果基线'+(res.perf_imported?' + '+res.perf_imported+' 条性能基线':'')+'（模型: '+res.model+'）');if(currentBlTab==='eval')loadBaselineDB();if(currentBlTab==='perf')loadPerfBaselineDB();});return;}
      if(b.dataset.blDel){const[m,d]=b.dataset.blDel.split('|');if(confirm('删除 '+m+' 在 '+d+' 上的基线？')){await api('/baselines/'+encodeURIComponent(m)+'/'+encodeURIComponent(d),'DELETE');baseline=null;baselineRequest=null;loadBaselineDB();}return;}
      if(b.dataset.blDelRow){const d=b.dataset.blDelRow;const ds=baselineDB?.datasets.find(x=>x.dataset_id===d);if(!ds)return;if(confirm('删除 '+d+' 的所有基线（'+ds.entries.length+' 条）？')){for(const e of ds.entries)await api('/baselines/'+encodeURIComponent(e.model)+'/'+encodeURIComponent(d),'DELETE');baseline=null;baselineRequest=null;loadBaselineDB();}return;}
      if(b.dataset.perfBlDel){const[m,s,met]=b.dataset.perfBlDel.split('||');if(confirm('删除 '+m+' 在 '+s+' 的 '+met+'？')){await api('/perf-baselines/'+encodeURIComponent(m)+'/'+encodeURIComponent(s)+'/'+encodeURIComponent(met),'DELETE');loadPerfBaselineDB();}return;}
      if(b.dataset.perfBlDelRow){const[m,s]=b.dataset.perfBlDelRow.split('||');const entries=perfBaselineDB?.rows.filter(r=>r.model===m&&r.scenario===s)||[];if(confirm('删除 '+m+' 在 '+s+' 的所有指标（'+entries.length+' 条）？')){for(const e of entries)await api('/perf-baselines/'+encodeURIComponent(m)+'/'+encodeURIComponent(s)+'/'+encodeURIComponent(e.metric),'DELETE');loadPerfBaselineDB();}return;}
      if(b.id==='web-bl-add'){const dlg=q('#web-bl-dialog');if(!dlg.hidden){dlg.hidden=true;}else{showBaselineForm();}return;}
      if(b.id==='web-bl-form-submit'){await withButton(b,'保存中…',submitBaselineForm);return;}
      if(b.id==='web-bl-dialog-close'){q('#web-bl-dialog').hidden=true;return;}
      if(b.id==='web-perf-bl-add'){const dlg=q('#web-perf-bl-dialog');if(!dlg.hidden){dlg.hidden=true;}else{showPerfBaselineForm();}return;}
      if(b.id==='web-perf-bl-form-submit'){await withButton(b,'保存中…',submitPerfBaselineForm);return;}
      if(b.id==='web-perf-bl-dialog-close'){q('#web-perf-bl-dialog').hidden=true;return;}
      if(b.dataset.blTab){currentBlTab=b.dataset.blTab;qa('[data-bl-tab]').forEach(t=>t.setAttribute('aria-pressed',String(t.dataset.blTab===currentBlTab)));q('#web-bl-eval-panel').hidden=currentBlTab!=='eval';q('#web-bl-perf-panel').hidden=currentBlTab!=='perf';if(currentBlTab==='eval')loadBaselineDB();else loadPerfBaselineDB();return;}
      if(b.id==='web-bl-rollback-prev'){if(confirm('确定回退到上一版基线？当前状态将自动备份。'))await withButton(b,'回退中…',async()=>{const res=await api('/baselines/rollback','POST',{target:'previous'});baseline=null;baselineRequest=null;feedback('已回退到 '+res.label+'（'+displayDate(res.restored_from)+'）');loadBaselineDB();loadPerfBaselineDB();});return;}
      if(b.id==='web-bl-rollback-1d'){if(confirm('确定回退到1天前的基线？当前状态将自动备份。'))await withButton(b,'回退中…',async()=>{const res=await api('/baselines/rollback','POST',{target:'1day'});baseline=null;baselineRequest=null;feedback('已回退到 '+displayDate(res.restored_from));loadBaselineDB();loadPerfBaselineDB();});return;}
      if(b.id==='web-bl-rollback-7d'){if(confirm('确定回退到7天前的基线？当前状态将自动备份。'))await withButton(b,'回退中…',async()=>{const res=await api('/baselines/rollback','POST',{target:'7days'});baseline=null;baselineRequest=null;feedback('已回退到 '+displayDate(res.restored_from));loadBaselineDB();loadPerfBaselineDB();});return;}
      if(b.id==='web-bl-snapshots'){const panel=q('#web-bl-snapshots-panel');if(!panel.hidden){panel.hidden=true;}else{await loadSnapshots();}return;}
      if(b.dataset.snapshotRestore){if(confirm('确定回退到此快照？当前状态将自动备份。'))await withButton(b,'回退中…',async()=>{const res=await api('/baselines/rollback','POST',{target:'snapshot:'+b.dataset.snapshotRestore});baseline=null;baselineRequest=null;feedback('已回退到 '+displayDate(res.restored_from));loadBaselineDB();loadPerfBaselineDB();q('#web-bl-snapshots-panel').hidden=true;});return;}
      if(b.dataset.taskTab){state.taskTab=b.dataset.taskTab;lastTaskRenderStatus=null;renderTask();return;}
      if(b.dataset.stage){state.stage=Number(b.dataset.stage);returnToTest=false;render();if(state.stage===3)await withButton(b,'加载历史…',loadTasks);if(state.stage===5)refreshSystemStats(true);if(state.stage===6){if(currentBlTab==='eval')loadBaselineDB();else loadPerfBaselineDB();}return;}
      if(b.dataset.mode){state.mode=b.dataset.mode;render();save();invalidateChecks();}
    }catch(error){showError(error);}
  });
  root.addEventListener('change',e=>{const el=e.target;
    if(el.id==='th-all-datasets'){state.all=el.checked;renderDatasets();}
    if(el.dataset.dataset){state.datasets=el.checked?[...new Set([...state.datasets,el.dataset.dataset])]:state.datasets.filter(id=>id!==el.dataset.dataset);}
    update();save();invalidateChecks();
  });
  root.addEventListener('input',e=>{if(fields.includes(e.target.id)||e.target.id==='web-judge-key'){update();invalidateChecks();}});
  async function init(){
    try{
      const saved=JSON.parse(localStorage.getItem('thvv-test-form')||'null');
      if(saved){state.mode=['perf','eval'].includes(saved.mode)?saved.mode:'perf';state.datasets=Array.isArray(saved.datasets)?saved.datasets:[];state.all=!!saved.all;state.connectionId=saved.connectionId||'';for(const id of fields)if(typeof saved.fields?.[id]==='string')q('#'+id).value=saved.fields[id];}
      syncAllDropdowns();
      render();
      const meta=await api('/meta');
      for(const [id,value] of meta.judge_defaults?[['th-judge-model',meta.judge_defaults.model],['th-judge-url',meta.judge_defaults.url]]:[]){
        q('#'+id).placeholder='框架默认：'+value;
        q('#'+id).parentElement.querySelector('.th-default').textContent='未配置时默认：'+value+'。';
      }
      if(!meta.engine.ready){q('#web-status').textContent='页面已就绪。可在下方分别检查、准备运行环境和数据。';q('#web-status').hidden=false;}
      if(meta.perf_metrics)perfMetricsList=meta.perf_metrics;
      await Promise.all([loadObjects(),loadTasks(),loadBaseline()]);
      environmentJob=await api('/environment');
      refreshSystemStats();
      if(environmentJob&&environmentJob.status==='running'){
        environmentBusy=true;environmentScope=environmentJob.scope||'environment';environmentAction=environmentJob.action||'check';
        renderEnvironment();
        followEnvironment(environmentJob).catch(showError).finally(()=>{environmentBusy=false;environmentButtons();});
      }else{
        if(environmentJob)environmentJob=null;
        renderEnvironment();
      }
    }catch(error){showError(error);}
  }
  setInterval(()=>{if(document.hidden||leavingSession)return;loadTasks().catch(showError);if(state.stage===4)refreshTask().catch(showError);if(!environmentBusy&&environmentJob?.status==='running')api('/environment').then(job=>{environmentJob=job;renderEnvironment();}).catch(showError);},2000);

  async function loadBaselineDB(){try{baselineDB=await api('/baselines');renderBaselineCards();}catch(e){showError(e);}}
  function renderBaselineCards(){
    const el=q('#web-bl-cards');
    if(!baselineDB||!baselineDB.datasets.length){el.innerHTML='<p class="th-empty">暂无基线数据。点击"录入基线"添加，或在历史任务中"刷入基线"。</p>';return;}
    const models=baselineDB.models;
    const lookup={};
    baselineDB.datasets.forEach(ds=>ds.entries.forEach(e=>{lookup[ds.dataset_id+'||'+e.model]={score:e.score,source:e.source,updated:e.updated};}));
    const fmtTime=v=>{try{return new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}catch{return '—';}};
    const colScores=models.map(m=>{const vals=baselineDB.datasets.map(ds=>(lookup[ds.dataset_id+'||'+m]||{}).score).filter(v=>v!=null);return vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:0;});
    const header='<tr><th style="text-align:left;position:sticky;left:0;background:var(--th-panel);z-index:1;min-width:140px">数据集</th>'
      +models.map((m,i)=>'<th style="text-align:center;min-width:80px;font-size:12px;white-space:nowrap" title="均分 '+colScores[i].toFixed(1)+'">'+esc(m)+'</th>').join('')
      +'<th style="text-align:center;min-width:48px;font-size:12px">操作</th></tr>';
    const rows=baselineDB.datasets.map((ds,ri)=>{
      const bg=ri%2===0?'':'background:var(--th-bg);';
      return '<tr style="'+bg+'"><td style="text-align:left;font-weight:600;font-size:13px;position:sticky;left:0;z-index:1;white-space:nowrap;'+(bg||'background:var(--th-panel);')+'">'+esc(ds.dataset_id)+'</td>'
        +models.map(m=>{
          const cell=lookup[ds.dataset_id+'||'+m];
          if(!cell)return '<td style="text-align:center;color:var(--th-muted);'+bg+'">—</td>';
          const isTask=cell.source&&cell.source.startsWith('task:');
          return '<td style="text-align:center;font-variant-numeric:tabular-nums;font-size:13px;font-weight:500;color:var(--th-text);'+bg+'" title="'+esc(m)+' · '+(isTask?'任务导入':'手动')+' · '+fmtTime(cell.updated)+'">'
            +cell.score.toFixed(2)+'<button type="button" class="th-task-action th-task-delete" data-bl-del="'+esc(m)+'|'+esc(ds.dataset_id)+'" style="display:none;font-size:11px;margin-left:4px" title="删除">✕</button></td>';
        }).join('')
        +'<td style="text-align:center;font-size:11px;'+bg+'"><button type="button" class="th-task-action th-task-delete" data-bl-del-row="'+esc(ds.dataset_id)+'" title="删除整行" style="font-size:11px">删除行</button></td></tr>';
    }).join('');
    const lastUpdated=baselineDB.datasets.reduce((mx,ds)=>ds.updated>mx?ds.updated:mx,'');
    el.innerHTML='<section class="th-panel"><div class="th-table-wrap" style="max-height:70vh;overflow:auto"><table style="border-collapse:collapse;font-size:13px;width:max-content">'
      +'<thead style="position:sticky;top:0;background:var(--th-panel);z-index:2">'+header+'</thead>'
      +'<tbody>'+rows+'</tbody></table></div>'
      +'<p class="th-note" style="margin-top:10px">每格 hover 查看来源和更新时间。'+(lastUpdated?' 最后更新：'+fmtTime(lastUpdated):'')+'</p></section>';
  }
  function showBaselineForm(){
    const allDs=[...new Set([...datasets.map(d=>d.id),...(baselineDB?baselineDB.datasets.map(d=>d.dataset_id):[])])].sort();
    const dsOpts=allDs.map(id=>'<option value="'+esc(id)+'">').join('');
    const modelOpts=(baselineDB?baselineDB.models:[]).map(m=>'<option value="'+esc(m)+'">').join('');
    q('#web-bl-dialog').hidden=false;
    q('#web-bl-dialog').innerHTML='<section class="th-panel" style="margin-bottom:14px"><h3>录入基线</h3>'
      +'<div class="th-fields" style="margin-top:10px">'
      +'<label class="th-field">模型名称<input id="web-bl-model" list="web-bl-model-list" placeholder="输入或选择模型名" autocomplete="off"><datalist id="web-bl-model-list">'+modelOpts+'</datalist></label>'
      +'<label class="th-field">数据集<input id="web-bl-dataset" list="web-bl-dataset-list" placeholder="输入或选择数据集" autocomplete="off"><datalist id="web-bl-dataset-list">'+dsOpts+'</datalist></label>'
      +'<label class="th-field">分数<input id="web-bl-score" type="number" step="0.01" placeholder="0-100"></label>'
      +'</div><div style="display:flex;gap:8px;margin-top:12px">'
      +'<button type="button" class="th-link-button" id="web-bl-form-submit">保存</button>'
      +'<button type="button" class="th-link-button" id="web-bl-dialog-close">取消</button>'
      +'</div></section>';
  }
  async function submitBaselineForm(){
    const model=q('#web-bl-model').value.trim();
    const dataset=q('#web-bl-dataset').value;
    const score=parseFloat(q('#web-bl-score').value);
    if(!model||!dataset||isNaN(score)){feedback('请填写完整','warning');return;}
    try{await api('/baselines','POST',{model,dataset,score});q('#web-bl-dialog').hidden=true;baseline=null;baselineRequest=null;feedback('基线已保存');loadBaselineDB();}catch(e){showError(e);}
  }
  let perfBaselineDB=null,currentBlTab='eval';
  async function loadPerfBaselineDB(){try{perfBaselineDB=await api('/perf-baselines');renderPerfBaselineCards();}catch(e){showError(e);}}
  function renderPerfBaselineCards(){
    const el=q('#web-perf-bl-cards');
    if(!perfBaselineDB||!perfBaselineDB.rows.length){el.innerHTML='<p class="th-empty">暂无性能基线数据。点击"录入性能基线"添加，或在历史任务中"刷入基线"。</p>';return;}
    const scenarios=perfBaselineDB.scenarios;
    const models=perfBaselineDB.models;
    const metrics=perfBaselineDB.metrics;
    const fmtTime=v=>{try{return new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}catch{return '—';}};
    const lookup={};
    perfBaselineDB.rows.forEach(r=>{lookup[r.model+'||'+r.scenario+'||'+r.metric]={value:r.value,source:r.source,updated:r.updated};});
    let html='';
    scenarios.forEach(sc=>{
      const header='<tr><th style="text-align:left;position:sticky;left:0;background:var(--th-panel);z-index:1;min-width:120px">模型</th>'
        +metrics.map(m=>'<th style="text-align:center;min-width:90px;font-size:12px;white-space:nowrap">'+esc(m)+'</th>').join('')
        +'<th style="text-align:center;min-width:48px;font-size:12px">操作</th></tr>';
      const rows=models.filter(m=>perfBaselineDB.rows.some(r=>r.model===m&&r.scenario===sc)).map((m,ri)=>{
        const bg=ri%2===0?'':'background:var(--th-bg);';
        return '<tr style="'+bg+'"><td style="text-align:left;font-weight:600;font-size:13px;position:sticky;left:0;z-index:1;white-space:nowrap;'+(bg||'background:var(--th-panel);')+'">'+esc(m)+'</td>'
          +metrics.map(met=>{
            const cell=lookup[m+'||'+sc+'||'+met];
            if(!cell)return '<td style="text-align:center;color:var(--th-muted);'+bg+'">—</td>';
            const isTask=cell.source&&cell.source.startsWith('task:');
            return '<td style="text-align:center;font-variant-numeric:tabular-nums;font-size:13px;font-weight:500;color:var(--th-text);'+bg+'" title="'+esc(m)+' · '+esc(sc)+' · '+(isTask?'任务导入':'手动')+' · '+fmtTime(cell.updated)+'">'
              +(typeof cell.value==='number'?(Number.isInteger(cell.value)?cell.value.toLocaleString():cell.value.toFixed(4)):cell.value)+'</td>';
          }).join('')
          +'<td style="text-align:center;font-size:11px;'+bg+'"><button type="button" class="th-task-action th-task-delete" data-perf-bl-del-row="'+esc(m)+'||'+esc(sc)+'" title="删除该模型在此场景的所有指标" style="font-size:11px">删除</button></td></tr>';
      }).join('');
      if(rows)html+='<section class="th-panel" style="margin-bottom:12px"><h3>'+esc(sc)+'</h3><div class="th-table-wrap" style="max-height:60vh;overflow:auto"><table style="border-collapse:collapse;font-size:13px;width:max-content">'
        +'<thead style="position:sticky;top:0;background:var(--th-panel);z-index:2">'+header+'</thead>'
        +'<tbody>'+rows+'</tbody></table></div>'
        +'<p class="th-note" style="margin-top:8px">最后更新：'+fmtTime(perfBaselineDB.rows.filter(r=>r.scenario===sc).reduce((mx,r)=>r.updated>mx?r.updated:mx,''))+'</p></section>';
    });
    el.innerHTML=html||'<p class="th-empty">暂无性能基线数据。</p>';
  }
  function showPerfBaselineForm(){
    const modelOpts=(perfBaselineDB?perfBaselineDB.models:[]).map(m=>'<option value="'+esc(m)+'">').join('');
    const scOpts=(perfBaselineDB?perfBaselineDB.scenarios:[]).map(s=>'<option value="'+esc(s)+'">').join('');
    const metOpts=perfMetricsList.map(m=>'<option value="'+esc(m)+'">').join('');
    q('#web-perf-bl-dialog').hidden=false;
    q('#web-perf-bl-dialog').innerHTML='<section class="th-panel" style="margin-bottom:14px"><h3>录入性能基线</h3>'
      +'<div class="th-fields" style="margin-top:10px">'
      +'<label class="th-field">模型名称<input id="web-perf-bl-model" list="web-perf-bl-model-list" placeholder="输入或选择模型名" autocomplete="off"><datalist id="web-perf-bl-model-list">'+modelOpts+'</datalist></label>'
      +'<label class="th-field">测试场景<input id="web-perf-bl-scenario" list="web-perf-bl-sc-list" placeholder="如 1k·并发1" autocomplete="off"><datalist id="web-perf-bl-sc-list">'+scOpts+'</datalist></label>'
      +'<label class="th-field">指标名<input id="web-perf-bl-metric" list="web-perf-bl-met-list" placeholder="选择或输入指标" autocomplete="off"><datalist id="web-perf-bl-met-list">'+metOpts+'</datalist></label>'
      +'<label class="th-field">值<input id="web-perf-bl-value" type="number" step="any" placeholder="数值"></label>'
      +'</div><div style="display:flex;gap:8px;margin-top:12px">'
      +'<button type="button" class="th-link-button" id="web-perf-bl-form-submit">保存</button>'
      +'<button type="button" class="th-link-button" id="web-perf-bl-dialog-close">取消</button>'
      +'</div></section>';
  }
  async function submitPerfBaselineForm(){
    const model=q('#web-perf-bl-model').value.trim();
    const scenario=q('#web-perf-bl-scenario').value.trim();
    const metric=q('#web-perf-bl-metric').value.trim();
    const value=parseFloat(q('#web-perf-bl-value').value);
    if(!model||!scenario||!metric||isNaN(value)){feedback('请填写完整','warning');return;}
    try{await api('/perf-baselines','POST',{model,scenario,metric,value});q('#web-perf-bl-dialog').hidden=true;feedback('性能基线已保存');loadPerfBaselineDB();}catch(e){showError(e);}
  }
  async function loadSnapshots(){
    try{
      const data=await api('/baselines/snapshots');
      const panel=q('#web-bl-snapshots-panel');
      if(!data.snapshots.length){panel.hidden=false;panel.innerHTML='<section class="th-panel"><h3>快照历史</h3><p class="th-note">暂无快照。每次修改基线时会自动创建快照。</p></section>';return;}
      const rows=data.snapshots.map(s=>{
        const evalCount=JSON.parse(s.eval_data||'[]').length;
        const perfCount=JSON.parse(s.perf_data||'[]').length;
        return '<tr><td style="text-align:left;white-space:nowrap;font-size:12px">'+esc(displayDate(s.created_at))+'</td>'
          +'<td style="text-align:left;font-size:12px">'+esc(s.label)+'</td>'
          +'<td style="text-align:center;font-size:12px">'+evalCount+' 效果 / '+perfCount+' 性能</td>'
          +'<td style="text-align:center"><button type="button" class="th-task-action" data-snapshot-restore="'+s.id+'" style="font-size:11px">回退到此版本</button></td></tr>';
      }).join('');
      panel.hidden=false;
      panel.innerHTML='<section class="th-panel"><h3>快照历史</h3><p class="th-note" style="margin-bottom:8px">每次修改基线时自动创建快照，最多保留 200 条。</p>'
        +'<div class="th-table-wrap" style="max-height:40vh;overflow:auto"><table style="border-collapse:collapse;width:100%"><thead><tr><th>时间</th><th>操作</th><th>记录数</th><th>操作</th></tr></thead>'
        +'<tbody>'+rows+'</tbody></table></div></section>';
    }catch(e){showError(e);}
  }

  let sysStats=null;
  let _prevNet={},_prevNetTime=0;
  const CHART_POINTS=60;
  const _history={cpu:[],mem:[],netRx:[],netTx:[]};
  function _pushHist(arr,v){arr.push(v);if(arr.length>CHART_POINTS)arr.shift();}

  function drawChart(canvasId,series,maxY,labels){
    const canvas=q('#'+canvasId);if(!canvas)return;
    const dpr=window.devicePixelRatio||1;
    const rect=canvas.getBoundingClientRect();
    canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const w=rect.width,h=rect.height;
    const pad={top:10,right:12,bottom:24,left:46};
    const cw=w-pad.left-pad.right,ch=h-pad.top-pad.bottom;
    function _resolveCSSColor(varExpr,fb){const el=document.createElement('div');el.style.cssText='background-color:var('+varExpr+','+fb+');position:absolute;visibility:hidden';root.appendChild(el);const c=getComputedStyle(el).backgroundColor;el.remove();return c;}
    const panelBg=_resolveCSSColor('--th-panel','#fff');
    const lineBg=_resolveCSSColor('--th-line','#dfe5ec');
    const mutedColor=_resolveCSSColor('--th-muted','#637183');
    ctx.clearRect(0,0,w,h);
    ctx.fillStyle=panelBg;ctx.fillRect(0,0,w,h);
    ctx.font='10px -apple-system,BlinkMacSystemFont,sans-serif';
    ctx.textBaseline='middle';
    for(let i=0;i<=4;i++){
      const y=pad.top+ch*(1-i/4);
      ctx.strokeStyle=lineBg;ctx.lineWidth=0.6;
      ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(pad.left+cw,y);ctx.stroke();
      ctx.fillStyle=mutedColor;ctx.textAlign='right';
      const lbl=labels?labels(maxY*i/4):Math.round(maxY*i/4)+'%';
      ctx.fillText(lbl,pad.left-6,y);
    }
    ctx.strokeStyle=lineBg;ctx.lineWidth=0.8;
    ctx.beginPath();ctx.moveTo(pad.left,pad.top);ctx.lineTo(pad.left,pad.top+ch);ctx.lineTo(pad.left+cw,pad.top+ch);ctx.stroke();
    ctx.fillStyle=mutedColor;ctx.textAlign='left';ctx.textBaseline='top';
    ctx.fillText('3 分钟前',pad.left,pad.top+ch+6);
    ctx.textAlign='right';ctx.fillText('现在',pad.left+cw,pad.top+ch+6);
    ctx.textAlign='center';ctx.fillText(Math.floor(CHART_POINTS/2*3/60)+'分钟前',pad.left+cw/2,pad.top+ch+6);
    const colors=['#4c8bf5','#f5a623','#34c759','#ff3b30'];
    const fills=['rgba(76,139,245,0.12)','rgba(245,166,35,0.10)','rgba(52,199,89,0.10)','rgba(255,59,48,0.10)'];
    series.forEach((data,si)=>{
      if(!data.length)return;
      const step=cw/(CHART_POINTS-1);const offset=CHART_POINTS-data.length;
      ctx.strokeStyle=colors[si%colors.length];ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';
      ctx.beginPath();
      data.forEach((v,i)=>{
        const x=pad.left+(offset+i)*step;
        const y=pad.top+ch*(1-Math.min(v,maxY)/maxY);
        i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
      });
      ctx.stroke();
      const lastX=pad.left+(offset+data.length-1)*step;const firstX=pad.left+offset*step;
      ctx.lineTo(lastX,pad.top+ch);ctx.lineTo(firstX,pad.top+ch);ctx.closePath();
      ctx.fillStyle=fills[si%fills.length];ctx.fill();
    });
  }

  function formatBytes(b){if(!b&&b!==0)return '—';if(b>=1073741824)return (b/1073741824).toFixed(1)+'G';if(b>=1048576)return (b/1048576).toFixed(0)+'M';return (b/1024).toFixed(0)+'K';}
  function formatDuration(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return (d?d+'天 ':'')+(h?h+'小时 ':'')+(m?m+'分钟':'<1分钟');}
  function fmtRate(v){return v>=1048576?(v/1048576).toFixed(1)+' MB/s':v>=1024?(v/1024).toFixed(0)+' KB/s':'0 KB/s';}
  function meterHTML(value,max,label){
    const pct=max?Math.round(100*value/max):0;
    const warn=pct>=70&&pct<90?'data-warn':'';const danger=pct>=90?'data-danger':'';
    return '<div class="th-meter" '+warn+danger+'><progress max="'+max+'" value="'+value+'"></progress><div class="th-meter-label"><span>'+esc(label)+'</span><span>'+formatBytes(value)+' / '+formatBytes(max)+' ('+pct+'%)</span></div></div>';
  }
  function renderSystemBar(){
    if(!sysStats)return;
    const cpu=sysStats.cpu,mem=sysStats.memory,disks=sysStats.disk||[];
    q('#web-sys-cpu').textContent='CPU '+cpu.total_percent+'%';
    q('#web-sys-mem').textContent='内存 '+formatBytes(mem.used)+'/'+formatBytes(mem.total);
    q('#web-sys-disk').textContent=disks.length?'磁盘 '+disks.map(d=>d.mount+' '+formatBytes(d.used)+'/'+formatBytes(d.total)).join(' · '):'磁盘 —';
  }
  function renderSystemPanel(){
    if(!sysStats||state.stage!==5)return;
    const s=sysStats,sys=s.system,cpu=s.cpu,mem=s.memory,disk=s.disk||[];
    const net=s.network||{},ifaces=net.interfaces||[];
    const rxRate=ifaces.reduce((sum,n)=>{const prev=_prevNet[n.name];return sum+(prev?Math.max(0,(n.rx_bytes-prev.rx_bytes)/((s.timestamp-(_prevNetTime||s.timestamp))||1)):0);},0);
    const txRate=ifaces.reduce((sum,n)=>{const prev=_prevNet[n.name];return sum+(prev?Math.max(0,(n.tx_bytes-prev.tx_bytes)/((s.timestamp-(_prevNetTime||s.timestamp))||1)):0);},0);
    _pushHist(_history.cpu,cpu.total_percent);
    _pushHist(_history.mem,Math.round(100*mem.used/mem.total));
    _pushHist(_history.netRx,rxRate);_pushHist(_history.netTx,txRate);
    q('#web-sys-info').textContent=esc(sys.os)+' · '+esc(sys.arch)+' · '+cpu.count+' 核 · '+esc(sys.hostname)+' · 运行 '+formatDuration(s.uptime)+' · 负载 '+s.load['1m']+'/'+s.load['5m']+'/'+s.load['15m'];
    const memPct=Math.round(100*mem.used/mem.total);
    const worstDisk=disk.reduce((a,b)=>((b.used/b.total)>(a.used/a.total)?b:a),disk[0]);
    const diskPct=worstDisk?Math.round(100*worstDisk.used/worstDisk.total):0;
    q('#web-sys-cards').innerHTML=[
      {label:'CPU',value:cpu.total_percent+'%',color:cpu.total_percent>=90?'#d64545':cpu.total_percent>=70?'#e8a630':'#28a745'},
      {label:'内存',value:memPct+'%',sub:formatBytes(mem.used)+' / '+formatBytes(mem.total),color:memPct>=90?'#d64545':memPct>=70?'#e8a630':'#28a745'},
      {label:'磁盘',value:diskPct+'%',sub:disk.length?disk.map(d=>d.mount+' '+formatBytes(d.used)+'/'+formatBytes(d.total)).join(' · '):'—',color:diskPct>=90?'#d64545':diskPct>=70?'#e8a630':'#28a745'},
      {label:'网络',value:'↓'+fmtRate(rxRate),sub:'↑'+fmtRate(txRate),color:'#2b5ad7'},
    ].map(c=>'<div class="th-monitor-card"><div class="th-card-label">'+c.label+'</div><div class="th-card-value" style="color:'+c.color+'">'+c.value+'</div>'+(c.sub?'<div class="th-card-label">'+c.sub+'</div>':'')+'</div>').join('');
    function dynamicMax(arr,minCeil){const peak=Math.max(...arr,0);const ceil=Math.ceil(peak/10)*10+10;return Math.max(ceil,minCeil||10);}
    drawChart('web-chart-cpu',[_history.cpu],dynamicMax(_history.cpu,10));
    drawChart('web-chart-mem',[_history.mem],dynamicMax(_history.mem,10));
    const netMax=Math.max(1024,..._history.netRx,..._history.netTx)*1.3;
    drawChart('web-chart-net',[_history.netRx,_history.netTx],netMax,v=>v>=1048576?(v/1048576).toFixed(1)+'M/s':v>=1024?(v/1024).toFixed(0)+'K/s':'0');
    q('#web-sys-cpu-detail').innerHTML='<div style="display:flex;flex-wrap:wrap;gap:4px;justify-content:center">'+cpu.cores.map((pct,i)=>'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-variant-numeric:tabular-nums;background:'+(pct>=90?'#fff3f3':pct>=70?'#fff8ec':'var(--th-bg)')+';color:'+(pct>=90?'#d64545':pct>=70?'#e8a630':'var(--th-muted)')+'">核心'+i+' '+pct+'%</span>').join('')+'</div>';
    q('#web-sys-mem-detail').innerHTML='<div style="display:flex;gap:16px;flex-wrap:wrap;justify-content:center;font-size:12px;color:var(--th-muted)"><span>可用 '+formatBytes(mem.available)+'</span><span>缓存 '+formatBytes(mem.cached)+'</span><span>Swap '+formatBytes(mem.swap_used)+' / '+formatBytes(mem.swap_total)+'</span></div>';
    const netLegend='<span style="display:inline-block;width:10px;height:3px;background:#4c8bf5;border-radius:2px;vertical-align:middle;margin-right:3px"></span>接收 <span style="display:inline-block;width:10px;height:3px;background:#f5a623;border-radius:2px;vertical-align:middle;margin:0 3px 0 10px"></span>发送';
    let netDetail=ifaces.map(n=>{const prev=_prevNet[n.name];const dt=(s.timestamp-(_prevNetTime||s.timestamp))||1;const rx=prev?Math.max(0,(n.rx_bytes-prev.rx_bytes)/dt):0;const tx=prev?Math.max(0,(n.tx_bytes-prev.tx_bytes)/dt):0;return esc(n.name)+' ↓'+fmtRate(rx)+' ↑'+fmtRate(tx);}).join(' · ');
    q('#web-sys-net-detail').innerHTML='<div style="font-size:12px;color:var(--th-muted)">'+netLegend+'<span style="margin-left:14px">'+netDetail+'</span></div>';
    const _newNet={};ifaces.forEach(n=>{_newNet[n.name]={rx_bytes:n.rx_bytes,tx_bytes:n.tx_bytes};});
    _prevNet=_newNet;_prevNetTime=s.timestamp;
    q('#web-sys-disk-detail').innerHTML=disk.map(d=>meterHTML(d.used,d.total,d.mount)).join('')||'<p class="th-note">无磁盘信息</p>';
    const docker=s.docker||[];
    const dp=q('#web-sys-docker-panel');
    if(docker.length){dp.hidden=false;q('#web-sys-docker-detail').innerHTML='<div class="th-table-wrap"><table><thead><tr><th>容器</th><th>CPU</th><th>内存</th><th>进程数</th></tr></thead><tbody>'+docker.map(c=>'<tr><td style="text-align:left">'+esc(c.name)+'</td><td>'+esc(c.cpu_percent)+'</td><td>'+esc(c.mem_usage)+'</td><td>'+esc(c.pids)+'</td></tr>').join('')+'</tbody></table></div>';}else{dp.hidden=true;}
    const procs=s.processes||[];
    q('#web-sys-proc-detail').innerHTML=procs.length?'<div class="th-table-wrap"><table><thead><tr><th>标签</th><th>PID</th><th>进程</th><th>CPU 时间</th><th>内存</th><th>线程</th></tr></thead><tbody>'+procs.map(p=>'<tr><td style="text-align:left">'+esc(p.label)+'</td><td>'+p.pid+'</td><td>'+esc(p.comm)+'</td><td>'+p.cpu_seconds+'s</td><td>'+formatBytes(p.rss_bytes)+'</td><td>'+p.threads+'</td></tr>').join('')+'</tbody></table></div>':'<p class="th-note">无活跃子进程</p>';
  }
  async function refreshSystemStats(full=false){try{sysStats=await api('/system/stats'+(full?'':'?light=1'));renderSystemBar();renderSystemPanel();}catch{}}
  setInterval(()=>{if(document.hidden||leavingSession)return;refreshSystemStats(state.stage===5);},1000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!leavingSession)loadTasks().catch(showError);});
  window.addEventListener('pageshow',event=>{if(event.persisted)window.location.reload();});
  init();
})();
