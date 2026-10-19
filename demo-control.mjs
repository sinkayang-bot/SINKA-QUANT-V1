// Real OKX demo control is isolated from the browser paper simulator.
export function controlBase(value) {
  const url=new URL(value);
  if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash ||
    url.hostname!=='sinka-okx-demo-control-361301529939.asia-east1.run.app' ||
    !['','/'].includes(url.pathname)) throw new Error('invalid_control_url');
  return url.origin;
}
export function createDemoControl({getUser,fetcher=fetch,onChange=()=>{}}) {
  let base='',pending=false,version=0;
  const emit=value=>onChange(value);
  return {
    configure(value){base=controlBase(value);version++;emit({phase:'configured'});},
    clear(){base='';version++;pending=false;emit({phase:'signed_out'});},
    async request(action=null){
      if(pending)throw new Error('request_pending');
      const user=getUser();if(!user || !base)throw new Error('login_and_service_required');
      if(action && !['start','stop','recover','audit','settle'].includes(action))throw new Error('unsupported_action');
      const epoch=version,destination=base;pending=true;
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
      } finally {if(epoch===version)pending=false;}
    }
  };
}

export function mountDemoControl({getUser}) {
  const host=document.getElementById('okx-demo-control');if(!host)return;
  const output=document.getElementById('demo-control-result'),notice=document.getElementById('demo-control-notice');
  let timer=null;
  const control=createDemoControl({getUser,onChange:state=>{
    if(state.phase==='status'){
      const s=state.status;
      notice.textContent=(s.runner_online?'執行器已連線':'執行器離線，畫面為歷史紀錄')+' · '+s.phase+(s.busy?' · 執行中':'');
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
