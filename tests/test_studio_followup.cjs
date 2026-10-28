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
