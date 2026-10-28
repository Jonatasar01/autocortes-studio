// Node-only regressions: exercise the actual Studio functions with minimal DOM controls.
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('app/static/studio.js','utf8');
const controls={};const $=id=>controls[id]??=( {value:'',checked:false,disabled:false,hidden:false} );
const context=vm.createContext({$,state:{profiles:{}},current:()=>({edit_plans:{p:{settings:{subtitle_style:'classica',framing:'blur',normalize_audio:false}}}}),
 exportPlanName:()=> 'p',profileTouched:()=>{$('profileSel').value=''},Boolean});
vm.runInContext(source.slice(source.indexOf('function syncSubtitleStyle()'),source.indexOf("$('subtitles').addEventListener('change', syncSubtitleStyle)")),context);
$('subtitles').checked=true;$('subtitleStyle').value='dinamica';$('profileSel').value='b:podcast';
context.syncSubtitleStyle();
assert.equal($('subtitleStyle').value,'classica');assert.equal($('framing').value,'blur');assert.equal($('normalize').checked,false);
assert.equal($('subtitleStyle').disabled,true);assert.equal($('profileSel').value,'');
context.exportPlanName=()=>null;context.syncSubtitleStyle();assert.equal($('subtitleStyle').disabled,false);assert.equal($('framing').disabled,false);
console.log('Studio profile regressions passed');

(async()=>{
 const c=vm.createContext({Error,Promise,withHint:x=>x,CSRF:{'X-AutoCortes':'1'},JSON});
 vm.runInContext(source.slice(source.indexOf('// Credentials stay'),source.indexOf('// O que fazer quando')),c);
 let requests=0, prompts=0;
 c.fetch=async()=>({ok:++requests>1,status:requests===1?401:200,json:async()=>requests===1?{code:'auth_required'}:{ok:true}});
 c.requestAuthentication=async()=>{prompts++;return true};
 assert.equal((await c.api('/test','POST',{})).ok,true);assert.equal(requests,2);assert.equal(prompts,1);
 requests=0;prompts=0;c.fetch=async()=>{requests++;return {ok:false,status:401,json:async()=>({code:'auth_required'})}};
 await assert.rejects(c.api('/test'),e=>e.code==='auth_required');assert.equal(requests,2);assert.equal(prompts,1);
 requests=0;c.requestAuthentication=async()=>false;
 await assert.rejects(c.api('/test'),e=>e.code==='auth_required');assert.equal(requests,1);
 c.fetch=async()=>({ok:false,status:413,json:async()=>{throw Error('HTML')}});
 await assert.rejects(c.api('/test'),e=>e.code==='upload_too_large'&&e.message.includes('túnel'));
 const uploadSource=source.slice(source.indexOf('function uploadFile('),source.indexOf("$('uploadForm').onsubmit"));
 c.$=$;c.URLSearchParams=URLSearchParams;
 c.XMLHttpRequest=class {open(){}setRequestHeader(){}upload={};send(){this.status=413;this.responseText='<html>too large</html>';this.onload()}};
 vm.runInContext(uploadSource,c);
 await assert.rejects(c.uploadFile({name:'sample.mp4'},{}),e=>e.code==='upload_too_large');
 console.log('Studio auth/upload regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1});

