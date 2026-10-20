// Explicit physical acceptance: temporary TouchProbe fixture, Lion USB only.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {connect,call,ROOT,UDID} from './mcp_client.mjs';
const udid=UDID;
assert.ok(udid,'Pass --udid from current USB discovery');
const out=ROOT+'.artifacts/toolkit-supervisor-20261020/';
await mkdir(out,{recursive:true});
const bundle='org.openrevive.TouchProbe', location={udid}, phone={target:'iphone',udid};
const field={label:'Probe text',role:'text-field',owner:bundle}, button={label:'Apply probe',role:'button',owner:bundle}, status={label:'Probe result',role:'label',owner:bundle};
const value=(selector,value)=>({state:'value',selector,value});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const packageRoot=process.argv.includes('--package')?process.argv[process.argv.indexOf('--package')+1]:undefined;
const a=await connect(packageRoot),b=await connect(packageRoot);let c;
const metrics=[];
async function save(name,data){await writeFile(out+name+'.json',JSON.stringify(data,null,2)+'\n');return data;}
async function run(conn,name,args,label,expectError=false){const start=performance.now();const x=await call(conn,name,args,expectError,{timeout:180000});metrics.push({phase:label,tool:name,elapsedMs:Math.round(performance.now()-start)});await save(label,x.result||x);console.log(label,x.result?.code||x.result?.operation?.code|| (x.error?'REFUSED':'OK'));return x.error?x:(x.result||x);}
async function reserve(conn){return (await call(conn,'device_operation',{udid,action:'reserve'})).result.operationId;}
async function phase(op,expected,observer=b,owner=a){const end=Date.now()+90000;while(Date.now()<end){const h=(await call(observer,'device_operation',{udid,action:'health'})).result;const j=h.busy.find(j=>j.operationId===op);if(j){const trace=(await call(owner,'device_operation',{udid,action:'trace',operationId:op})).result;if(j.phase===expected||trace.events.some(e=>e.phase===expected))return h;}if(!j&&h.device.mode!=='ready')throw Error(JSON.stringify(h));await sleep(250);}throw Error('No phase '+expected);}

async function settled(conn,op){const end=Date.now()+90000;while(Date.now()<end){const x=(await call(conn,'device_operation',{udid,action:'status',operationId:op})).result;if(x.terminal&&x.executorQuiescent)return x;await sleep(250);}throw Error('Executor did not quiesce');}
let sessionB,container;let count=0;
try{
 const sa=await run(a,'device_session',location,'shared-a'),sb=await run(b,'device_session',location,'shared-b');sessionB=sb.sessionId;
 assert.equal(sa.supervisor.pid,sb.supervisor.pid);assert.equal(sa.supervisor.actors[0].pid,sb.supervisor.actors[0].pid);
 const oa=await run(a,'ui_observe',{udid,nodeLimit:12,byteLimit:8192},'consumer-a');assert.equal(oa.context.frontmostBundleId,bundle);
 const ob=await run(b,'ui_observe',{udid,since:oa.revision,nodeLimit:12,byteLimit:8192},'consumer-b');assert.match(ob.resetReason,/consumer baseline/);
 const tree=await run(a,'get_ui_tree',{...phone,pageSize:150},'probe-full-tree');container=tree.owners.find(o=>o.bundleId===bundle).containerPath;
 const old=tree.nodes.find(n=>n.label==='Probe result');count=Number(/^Applied (\d+):/.exec(old.value)?.[1]||0);
 const text='Supervisor '+Date.now()+' · Привет 日本語';
 const edit={udid,bundle,selector:field,action:'set-text',value:text,expect:value(field,text),timeout:60};
 const op=await reserve(a);const first=await run(a,'ui_perform',{...edit,operationId:op},'perform-text');assert.equal(first.code,'VERIFIED');assert.equal(first.dispatches,1);
 const ensured=await run(a,'ui_perform',{...edit,mode:'ensure'},'ensure-text');assert.equal(ensured.code,'ALREADY_SATISFIED');assert.equal(ensured.dispatches,0);
 const duplicate=await run(a,'ui_perform',{...edit,operationId:op},'deduplicated-text');assert.ok(duplicate.deduplicated);
 const guard=await run(a,'ui_perform',{...edit,bundle:'com.apple.Preferences'},'wrong-context',true);assert.equal(guard.code,'CONTEXT_CHANGED');assert.equal(guard.effect,'none');
 count++;
 const applied=await run(a,'ui_perform',{udid,bundle,selector:button,expect:value(status,`Applied ${count}: ${text}`),timeout:60},'perform-apply');assert.equal(applied.code,'VERIFIED');
 const receipt=await run(a,'read_file',{...phone,path:container+'/Documents/probe-result.json',maxBytes:100000},'receipt-first');assert.equal(JSON.parse(receipt.data).text,text);assert.equal(JSON.parse(receipt.data).clicks,count);
 const text2=text+' workflow';count++;
 const workflow=await run(a,'ui_workflow',{udid,bundle,timeout:120,maxDispatches:2,steps:[
  {kind:'assert',expect:value(field,text)},
  {kind:'perform',selector:field,action:'set-text',value:text2,expect:value(field,text2)},
  {kind:'perform',selector:button,expect:value(status,`Applied ${count}: ${text2}`)},
  {kind:'branch',expect:value(status,`Applied ${count}: ${text2}`),then:[{kind:'assert',expect:value(field,text2)}]},
 ]},'workflow-text-save');assert.equal(workflow.code,'VERIFIED');assert.equal(workflow.dispatches,2);
 const cancelId=await reserve(a),cancelArgs={udid,bundle,operationId:cancelId,selector:button,expect:value(status,'NEVER_EXPECTED_'+Date.now()),timeout:120};
 const pending=call(a,'ui_perform',cancelArgs,false,{timeout:180000});
 const busy=await phase(cancelId,'verifying');await save('busy-owner',busy);
 const rejected=await run(b,'ui_perform',{udid,bundle,selector:button,expect:{state:'present',selector:button}},'second-writer',true);assert.match(rejected.error,/operation in progress/);assert.equal(rejected.result.owner.operationId,cancelId);
 const cached=await run(b,'ui_observe',{udid,cached:true},'cached-during-write');assert.equal(cached.historical,true);
 await run(b,'read_logs',{udid,duration:.2,limit:2,process:'OpenReviveTouchProbe'},'telemetry-during-write');
 await run(a,'device_operation',{udid,action:'cancel',operationId:cancelId},'cancel-request');
 const cancelled=(await pending).result;await save('cancel-result',cancelled);assert.equal(cancelled.code,'CANCELLED');assert.equal(cancelled.operation.executorQuiescent,true);assert.equal(cancelled.dispatches,1);count++;
 const after=JSON.parse((await run(b,'read_file',{...phone,path:container+'/Documents/probe-result.json'},'receipt-after-cancel')).data);assert.equal(after.clicks,count);
 await run(a,'device_operation',{udid,action:'trace',operationId:cancelId},'cancel-trace');
 const disconnectId=await reserve(b),waitArgs={udid,bundle,operationId:disconnectId,timeout:120,steps:[{kind:'wait',expect:value(status,'NEVER_DISCONNECT')} ]};
 const disconnected=call(b,'ui_workflow',waitArgs,false,{timeout:180000}).then(x=>({value:x}),e=>({error:e.message}));
 await phase(disconnectId,'verifying',a,b);await b.close();await disconnected;
 c=await connect(packageRoot);await run(c,'device_session',{udid,operation:'attach',sessionId:sessionB},'reconnect-attach');
 const resumed=await settled(c,disconnectId);await save('disconnect-result',resumed);assert.equal(resumed.code,'CANCELLED');assert.equal(resumed.effect,'none');
 const noReplay=await run(c,'ui_workflow',waitArgs,'disconnect-deduplicated');assert.equal(noReplay.deduplicated,true);
 const oldSnapshot=(await run(a,'ui_observe',{udid},'pre-handover')).snapshotId;
 const manual=await run(c,'device_operation',{udid,action:'handover'},'handover');assert.equal(manual.manualControlReady,true);
 await run(a,'ui_perform',{...edit,mode:'ensure'},'manual-input-refused',true);
 await run(c,'device_operation',{udid,action:'resume'},'resume');
 const stale=await run(a,'read_ui_tree',{snapshotId:oldSnapshot},'pre-handover-stale',true);assert.match(stale.error,/Unknown|expired/);
 let since;const samples=[];
 for(let i=0;i<5;i++){const start=performance.now(),observation=await run(a,'ui_observe',{udid,nodeLimit:20,byteLimit:8192,...(since?{since}: {})},'benchmark-observe-'+i);samples.push({elapsedMs:Math.round(performance.now()-start),sourceJsonBytes:observation.sourceJsonBytes,returnedBytes:observation.returnedBytes,mode:observation.mode});assert.ok(observation.returnedBytes<=8192);since=observation.revision;}
 await save('observation-benchmark',samples);
 for(let i=0;i<4;i++){const x=await run(a,'get_screenshot',{...phone,transport:'vnc',updateTimeout:.3,...(i===3?{crop:[0,0,320,240]}:{})},'vnc-'+i);assert.equal(x.freshness,'unverified');if(i===3)assert.deepEqual([x.width,x.height],[320,240]);}
 const full=await call(a,'get_screenshot',{...phone,transport:'native'});await writeFile(out+'native-full.png',Buffer.from(full.raw.content.find(c=>c.type==='image').data,'base64'));
 const region=[24,100,300,180];
 const crop=await call(a,'get_screenshot',{...phone,transport:'native',crop:region});await writeFile(out+'native-crop.png',Buffer.from(crop.raw.content.find(c=>c.type==='image').data,'base64'));await save('native-crop',crop.result);assert.deepEqual([crop.result.width,crop.result.height],region.slice(2));
 console.log(execFileSync(process.env.OPENREVIVE_PYTHON||'python3',[ROOT+'tests/png_pixels.py',out+'native-full.png',out+'native-crop.png',JSON.stringify(region)],{encoding:'utf8'}).trim());
 const artifact=await call(a,'device_artifact',{udid,artifactId:crop.result.artifact.artifactId});assert.equal(artifact.result.artifactId,crop.result.artifact.artifactId);
 await run(c,'device_artifact',{udid,artifactId:crop.result.artifact.artifactId},'artifact-other-owner',true);
 await run(a,'ui_workflow',{udid,timeout:90,steps:[{kind:'ensure_context',bundle:'com.apple.mobilesafari'}]},'safari-foreground');
 const listing=await run(a,'web_inspect',{udid,operation:'pages'},'web-pages');assert.ok(listing.pages.length);
 const page=listing.pages.find(p=>p.application==='com.apple.mobilesafari'&&p.title==='WakeApp')||listing.pages.find(p=>p.application==='com.apple.mobilesafari');assert.ok(page);
 const context=await run(a,'web_inspect',{udid,operation:'context',pageId:page.pageId},'web-context');assert.ok(context.documentEpoch);assert.ok(context.nativeGuard);
 const dom=await run(a,'web_inspect',{udid,operation:'dom',contextId:context.contextId,documentEpoch:context.documentEpoch,limit:20},'web-dom');assert.ok(dom.context);
 const js=await run(a,'web_inspect',{udid,operation:'evaluate',contextId:context.contextId,documentEpoch:context.documentEpoch,expression:'1+1'},'web-js');assert.equal(js.evaluation.result.value,2);
 await run(a,'web_inspect',{udid,operation:'evaluate',contextId:context.contextId,documentEpoch:'f'.repeat(32),expression:'1+1'},'web-stale-document',true);
 const refreshed=await run(a,'web_inspect',{udid,operation:'pages'},'web-pages-after-stale');
 const same=refreshed.pages.find(p=>p.application===page.application&&p.nativePageId===page.nativePageId);assert.ok(same);
 const guarded=await run(a,'web_inspect',{udid,operation:'context',pageId:same.pageId},'web-context-for-native-guard');
 await run(a,'ui_workflow',{udid,timeout:60,steps:[{kind:'ensure_context',bundle}]},'probe-return');
 const background=await run(a,'web_inspect',{udid,operation:'evaluate',contextId:guarded.contextId,documentEpoch:guarded.documentEpoch,expression:'1+1'},'web-background-refused',true);assert.match(background.error,/CONTEXT_CHANGED/);
 await run(a,'device_session',{udid,operation:'close',component:'all'},'explicit-close');
 const reconnected=await run(a,'ui_observe',{udid},'after-explicit-close');assert.equal(reconnected.context.frontmostBundleId,bundle);
 await save('metrics',metrics);await save('physical-acceptance',{passed:true,udid,packageRoot:packageRoot||ROOT+'plugins/openrevive',server:a.client.getServerVersion(),transport:'Lion SSH/usbmux',directControllerUsb:false,clicks:count,at:new Date().toISOString()});
 console.log('OPENREVIVE_SUPERVISOR_PHYSICAL_OK');
}catch(error){await save('physical-failure',{message:error.message,stack:error.stack,metrics});throw error;}
finally{await save('metrics-progress',metrics);await a.close();await b.close().catch(()=>{});if(c)await c.close();}
