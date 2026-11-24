(() => {
  const form=document.querySelector('#login-form');
  const button=document.querySelector('#login-submit');
  const error=document.querySelector('#login-error');
  const status=document.querySelector('#login-status');
  form.addEventListener('input',()=>{error.hidden=true;status.textContent='';});
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(button.disabled)return;
    button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在登录…';
    error.hidden=true;status.textContent='正在验证账号和密码';
    try{
      const response=await fetch('/api/auth/login',{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-THVV-Request':'1'},
        body:JSON.stringify({username:form.elements.username.value.trim(),password:form.elements.password.value})
      });
      const data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'登录失败，请稍后重试');
      form.elements.password.value='';status.textContent='登录成功，正在进入控制台…';
      window.location.replace('/');
    }catch(failure){
      error.textContent=failure instanceof TypeError?'暂时无法连接控制台，请稍后重试':failure.message;
      error.hidden=false;status.textContent='';
      form.elements.password.value='';form.elements.password.focus();
      button.disabled=false;button.removeAttribute('aria-busy');button.textContent='登录';
    }
  });
})();
