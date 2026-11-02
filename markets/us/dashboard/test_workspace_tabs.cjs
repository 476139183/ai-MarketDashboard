const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {test}=require('node:test');
const vm=require('node:vm');

function workspace(path,parent=null){
 const location=new URL(path,'http://localhost');
 const handlers={},frames=[],history=[];
 const view={inert:false,setAttribute(){},removeAttribute(){}};
 const window={location,addEventListener:(name,fn)=>handlers[name]=fn};
 window.parent=parent||window;
 const update=url=>{const parsed=new URL(url,location);location.href=parsed.href;};
 const context=vm.createContext({window,location,URL,
  history:{replaceState(_state,_title,url){update(url);},pushState(_state,_title,url){history.push(url);update(url);}},
  document:{title:'观市',querySelector:()=>view,documentElement:{style:{}},
   body:{appendChild:frame=>frames.push(frame)},createElement:()=>({style:{},hidden:false}),
   addEventListener:(name,fn)=>handlers[name]=fn},
 });
 vm.runInContext(readFileSync(join(__dirname,'static/workspace-tabs.js'),'utf8'),context);
 return {window,view,frames,history,
  click(href){let prevented=false;handlers.click({button:0,preventDefault(){prevented=true;},
    target:{closest:()=>({href:new URL(href,location).href,target:'',hasAttribute:()=>false})}});return prevented;},
  back(url){update(url);handlers.popstate();},
 };
}

test('切换工作区只创建一次另一页，返回时保留原文档与页内状态',()=>{
 const root=workspace('/?symbols=SNDK,MU');
 root.view.savedCards=['SNDK','MU'];
 assert.equal(root.click('/research'),true);
 const frame=root.frames[0];assert.ok(frame);assert.equal(root.view.inert,true);
 const research=workspace('/research',root.window);
 research.view.report={symbol:'SNDK',summary:'已完成的报告'};
 research.click('/');assert.equal(frame.hidden,true);assert.equal(root.view.inert,false);
 assert.deepEqual(root.view.savedCards,['SNDK','MU']);
 root.click('/research');assert.equal(root.frames.length,1);assert.equal(frame.hidden,false);
 assert.equal(research.view.report.symbol,'SNDK');
 assert.equal(frame.src,'/research');
 root.back('/?symbols=SNDK,MU');assert.equal(frame.hidden,true);
 root.back('/research');assert.equal(frame.hidden,false);
});

test('从研究页进入信号后返回仍使用同一工作区，其他链接正常跳转',()=>{
 const root=workspace('/research?symbol=NVDA');
 assert.equal(root.click('/backtest'),false);
 root.click('/');const signals=workspace('/',root.window);
 signals.click('/research');assert.equal(root.frames[0].hidden,true);
 root.click('/');assert.equal(root.frames.length,1);
});
