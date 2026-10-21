// Real OKX demo control is isolated from the browser paper simulator.
export function controlBase(value) {
  const url=new URL(value);
  if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash ||
    url.hostname!=='sinka-okx-demo-control-361301529939.asia-east1.run.app' ||
    !['','/'].includes(url.pathname)) throw new Error('invalid_control_url');
  return url.origin;
}
export function createDemoControl({getUser,fetcher=fetch,onChange=()=>{}}) {
  let base='',pending=null,version=0;
  const emit=value=>onChange(value);
  return {
    configure(value){
      const next=controlBase(value);
      if(next===base)return;
      base=next;version++;pending=null;emit({phase:'configured'});
    },
    clear(){base='';version++;pending=null;emit({phase:'signed_out'});},
    async request(action=null){
      const user=getUser();if(!user || !base)throw new Error('login_and_service_required');
      if(action && !['start','stop','recover','audit','settle'].includes(action))throw new Error('unsupported_action');
      const epoch=version,destination=base;
      if(pending){
        // Polls skip an existing request. One user command can wait for a GET,
        // but another command is never queued or automatically replayed.
        if(!action)return;
        if(pending.action)throw new Error('command_in_progress');
        await pending.done;
        if(epoch!==version || getUser()!==user)return;
        if(pending)throw new Error('command_in_progress');
      }
      let release;
      const current={action,done:new Promise(resolve=>release=resolve)};
      pending=current;
      try {
        const token=await user.getIdToken();
        if(epoch!==version || getUser()!==user) return;
        const response=await fetcher(destination+(action?'/api/demo/commands':'/api/demo/status'),{
          method:action?'POST':'GET',headers:{Authorization:'Bearer '+token,...(action?{'Content-Type':'application/json'}:{})},
          ...(action?{body:JSON.stringify({action,request_id:crypto.randomUUID()})}:{}),
          cache:'no-store',redirect:'error',credentials:'omit',signal:AbortSignal.timeout(20000)});
        const value=await response.json();
        if(epoch!==version || getUser()!==user)return;
        if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'service_unavailable');
        if(!action && (value.mode!=='okx_demo_control' || value.live_trading_enabled!==false))throw new Error('invalid_demo_response');
        emit(action?{phase:'command_accepted',action,...value}:{phase:'status',status:value});
        return value;
      } finally {if(pending===current)pending=null;release();}
    }
  };
}

export function runnerNotice(s) {
  const valid=Number.isSafeInteger(s.heartbeat_at)&&s.heartbeat_at>0;
  const heartbeat=valid?new Date(s.heartbeat_at).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}):'未提供';
  if(!s.runner_online)return `執行器離線，目前無法執行指令；最後心跳：${heartbeat}（台灣時間）。下方為歷史紀錄，${s.phase} 不代表在線。`;
  return '執行器已連線 · '+s.phase+(s.busy?' · 執行中':'')+(s.recovery_required?' · 需先恢復核對':'');
}

export function mountDemoControl({getUser}) {
  const host=document.getElementById('okx-demo-control');if(!host)return;
  const output=document.getElementById('demo-control-result'),notice=document.getElementById('demo-control-notice');
  let timer=null;
  const control=createDemoControl({getUser,onChange:state=>{
    if(state.phase==='status'){
      const s=state.status;
      notice.textContent=runnerNotice(s);
      output.textContent=JSON.stringify(s,null,2);
      host.querySelectorAll('[data-demo-action]').forEach(b=>b.disabled=!s.runner_online || s.command_pending || (s.busy && b.dataset.demoAction!=='stop'));
      host.querySelector('[data-demo-action="start"]').disabled=!s.runner_online || s.busy || s.command_pending || s.recovery_required;
      host.querySelector('[data-demo-action="stop"]').disabled=!s.runner_online || !s.busy || s.command_pending;
    } else notice.textContent=state.phase==='command_accepted'?'指令已送出，等待執行器回覆。':state.phase==='signed_out'?'尚未連線，請先登入。':'服務已設定，請更新狀態。';
  }});
  function failure(error){notice.textContent='操作未完成：'+error.message;}
  document.getElementById('demo-control-connect').addEventListener('click',async()=>{
    try{
      control.configure(document.getElementById('demo-control-url').value.trim());
      await control.request();
      if(timer)clearInterval(timer);
      timer=setInterval(()=>{if(document.visibilityState==='visible' && getUser())control.request().catch(failure);},5000);
    }catch(error){failure(error);}
  });
  host.querySelectorAll('[data-demo-action]').forEach(button=>button.addEventListener('click',async()=>{
    button.disabled=true;
    try{await control.request(button.dataset.demoAction);await control.request();}catch(error){failure(error);}
    finally{if(!['start','stop'].includes(button.dataset.demoAction))button.disabled=false;}
  }));
  window.addEventListener('pagehide',()=>{if(timer)clearInterval(timer);control.clear();});
  return {clear(){if(timer)clearInterval(timer);control.clear();output.textContent='';host.querySelectorAll('[data-demo-action]').forEach(b=>b.disabled=true);}};
}
