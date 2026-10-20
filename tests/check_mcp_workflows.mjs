import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {connect,call,image,select,record,OUT,UDID} from './mcp_client.mjs';
assert.ok(UDID,'--udid required');
const c=await connect();
const phone={target:'iphone',udid:UDID};
const mac={target:'mac'};
const evidence={};
const local=await mkdtemp(tmpdir()+'/openrevive-mcp-');
const remote='/tmp/'+local.split('/').at(-1);
const delay=ms=>new Promise(r=>setTimeout(r,ms));
try {
  const args=['space value',"single'quote",'$(echo literal)','`echo literal`','Привет 日本語','line1\nline2','--yes'];
  let result=(await call(c,'run_command',{...mac,argv:['/usr/bin/python','-c','import sys,os,json; print(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"env":os.environ["OPENREVIVE_TEST"]}))',...args],cwd:'/tmp',env:{OPENREVIVE_TEST:'literal $()`'}})).result;
  assert.deepEqual(JSON.parse(result.stdout),{args,cwd:'/private/tmp',env:'literal $()`'});
  evidence.argv=result;
  for(const aqua of [false,true]) {
    result=(await call(c,'run_command',{...mac,argv:['/bin/launchctl','managername'],aqua})).result;
    assert.equal(result.stdout.trim(),aqua?'Aqua':'Background');
  }
  const failed=await call(c,'run_command',{...mac,argv:['/usr/bin/python','-c','import sys; print("stdout receipt");sys.stderr.write("stderr receipt\\n");sys.exit(7)']},true);
  assert.equal(failed.result.exitCode,7); assert.match(failed.result.stderr,/stderr receipt/);
  const timeout=await call(c,'run_command',{...mac,argv:['/bin/sleep','10'],timeout:1},true);
  assert.equal(timeout.result.exitCode,124);
  const large=(await call(c,'run_command',{...mac,argv:['/usr/bin/python','-c','import sys;sys.stdout.write("x"*100000)']})).result;
  assert.equal(large.stdoutBytes,100000);assert.equal(large.stdout.length,65536);assert.equal(large.truncated,true);
  evidence.statuses={failed:failed.result,timeout:timeout.result,large:{...large,stdout:'omitted in evidence'}};
  const weird="quote' $ ` 日本語.txt";
  await writeFile(local+'/'+weird,'MCP sync Привет 日本語\n');
  await call(c,'sync_source',{localDirectory:local,remoteDirectory:remote});
  await call(c,'run_command',{...mac,argv:['/usr/bin/touch',remote+'/remote-only.txt']});
  await call(c,'sync_source',{localDirectory:local,remoteDirectory:remote});
  assert.equal((await call(c,'read_file',{...mac,path:remote+'/'+weird})).result.data,'MCP sync Привет 日本語\n');
  await call(c,'run_command',{...mac,argv:['/bin/test','-f',remote+'/remote-only.txt']});
  evidence.sync={weird,remoteOnlyPreserved:true};
  console.log('MCP command, timeout, bounded output and sync checks passed');
  const sdks=(await call(c,'run_workflow',{arguments:['xcode','sdks']})).result;
  assert.match(sdks.stdout,/iphoneos6.1/);evidence.sdks=sdks;
  const blocked=await call(c,'run_workflow',{arguments:['repair','accessibility','--prompt']},true);
  assert.match(blocked.error,/Interactive/);
  const phoneArgv=(await call(c,'run_command',{...phone,argv:['/bin/echo',...args]})).result;
  assert.ok(phoneArgv.stdout.includes(args[2]) && phoneArgv.stdout.includes(args[4]));
  evidence.phoneArgv=phoneArgv;
  // Real build, unsigned/AppSync install and launch through MCP workflows.
  const built=(await call(c,'run_workflow',{arguments:['device','--udid',UDID,'build-probe',local],timeout:240})).result;
  assert.equal(built.exitCode,0);evidence.build=built;
  const installed=(await call(c,'run_workflow',{arguments:['device','--udid',UDID,'install',local+'/OpenReviveTouchProbe.ipa'],timeout:240})).result;
  assert.equal(installed.exitCode,0);evidence.install=installed;
  console.log('MCP physical iPhone build/install passed');
  await call(c,'iphone_input',{udid:UDID,operation:'launch',bundle:'org.openrevive.TouchProbe'});
  let tree=(await call(c,'get_ui_tree',phone)).result;
  if(tree.systemContext.switcherVisible) {
    await call(c,'iphone_system_control',{snapshotId:tree.snapshotId,action:'dismiss-switcher'});
    tree=(await call(c,'get_ui_tree',phone)).result;
  }
  const reset=await select(c,tree,{label:'Reset touches',role:'button',owner:'org.openrevive.TouchProbe'});
  await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:reset.id});
  tree=(await call(c,'get_ui_tree',phone)).result;
  const container=tree.owners.find(o=>o.bundleId==='org.openrevive.TouchProbe').containerPath;
  const gesture={fingers:[{id:1,points:[{t:0,x:250,y:810},{t:.7,x:150,y:810}]},{id:2,points:[{t:0,x:390,y:810},{t:.7,x:490,y:810}]}]};
  const contacts=(await call(c,'iphone_gesture',{snapshotId:tree.snapshotId,bundle:'org.openrevive.TouchProbe',gesture})).result;
  const touch=JSON.parse((await call(c,'read_file',{...phone,path:container+'/Documents/multitouch-result.json',maxBytes:100000})).result.data);
  assert.ok(touch.maxSimultaneous>=2,JSON.stringify(touch));
  assert.ok(touch.pinchScale>1.2,JSON.stringify(touch));
  evidence.multitouch={contacts,receipt:touch};
  console.log('MCP independent multitouch receipt passed');
  tree=(await call(c,'get_ui_tree',phone)).result;
  evidence.switcher=(await call(c,'iphone_system_control',{snapshotId:tree.snapshotId,action:'switcher'})).result;
  tree=(await call(c,'get_ui_tree',phone)).result;
  assert.equal(tree.systemContext.surface,'app-switcher',JSON.stringify(tree.systemContext));
  await image(c,phone,'iphone-switcher');
  evidence.dismissSwitcher=(await call(c,'iphone_system_control',{snapshotId:tree.snapshotId,action:'dismiss-switcher'})).result;
  // Native layout guards reject an old Mac node after another controller moves
  // only the disposable probe window. No snapshot JSON can be forged by callers.
  const probe=(await call(c,'list_mac_apps')).result.items.find(a=>a.bundleId==='org.openrevive.Probe');
  assert.ok(probe);
  await call(c,'mac_input',{expectedPid:probe.pid,operation:'focus'});
  const old=(await call(c,'get_ui_tree',{...mac,pid:probe.pid})).result;
  const button=await select(c,old,{label:'Apply probe',role:'AXButton'});
  const win=old.nodes.find(n=>n.role==='AXWindow' && n.title==='OpenRevive GUI Probe');
  assert.ok(win?.frame);
  const other=await connect();
  try {
    const [x,y,w]=win.frame;
    await call(other,'mac_input',{expectedPid:probe.pid,operation:'drag',x:Math.round(x+w/2),y:Math.round(y+10),x2:Math.round(x+w/2+20),y2:Math.round(y+10)});
  } finally {await other.close();}
  const stale=await call(c,'act_ui',{snapshotId:old.snapshotId,nodeId:button.id},true);
  assert.match(stale.error,/changed/);evidence.changedLayout=stale.error;
  // Exercise the direct keyboard and click wrapper as well as AX form actions.
  let keyboardTree=(await call(c,'get_ui_tree',{...mac,pid:probe.pid})).result;
  const field=await select(c,keyboardTree,{role:'AXTextField'});
  const [fx,fy,fw,fh]=field.frame;
  await call(c,'mac_input',{expectedPid:probe.pid,operation:'click',x:Math.round(fx+fw/2),y:Math.round(fy+fh/2)});
  await call(c,'mac_input',{expectedPid:probe.pid,operation:'key',key:'a',modifiers:'cmd'});
  const keyboardText='MCP keyboard: Привет 日本語 '+Date.now();
  await call(c,'mac_input',{expectedPid:probe.pid,operation:'type',text:keyboardText});
  keyboardTree=(await call(c,'get_ui_tree',{...mac,pid:probe.pid})).result;
  const apply=await select(c,keyboardTree,{label:'Apply probe',role:'AXButton'});
  await call(c,'act_ui',{snapshotId:keyboardTree.snapshotId,nodeId:apply.id});
  const keyboardReceipt=JSON.parse((await call(c,'read_file',{...mac,path:'~/Library/Logs/OpenReviveProbe/result.json'})).result.data);
  assert.equal(keyboardReceipt.text,keyboardText);evidence.macKeyboard=keyboardReceipt;
  console.log('MCP system controls, changed layout rejection and Mac keyboard passed');
  // Retain a Mac handle while unrelated phone/cancellation checks run; verify
  // its real wall-clock expiry in a separate still-live server session.
  const expiration=await connect();
  try {
    const exp=(await call(expiration,'get_ui_tree',{...mac,pid:probe.pid})).result;
    const start=Date.now();
    const p=(await call(c,'get_ui_tree',phone)).result;
    const serverPid=c.transport.pid;
    const ps=execFileSync('/bin/ps',['-axo','pid=,ppid=,pgid=,command='],{encoding:'utf8'});
    const worker=ps.split('\n').map(s=>s.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(.+)$/)).find(m=>m && +m[2]===serverPid && m[4].includes('/mcp/backend.py'));
    assert.ok(worker,'Persistent Python worker not found');
    const group=+worker[3];
    const abort=new AbortController();
    const pending=c.client.callTool({name:'run_command',arguments:{...mac,argv:['/bin/sleep','20'],timeout:3}},{timeout:30000,signal:abort.signal}).then(()=>false,()=>true);
    await delay(500);
    const busy=await call(c,'list_targets',{discoverUsb:false},true);
    assert.match(busy.error,/in progress/);
    abort.abort();assert.equal(await pending,true);
    let ready=false;
    for(let n=0;n<12;n++) {
      await delay(800);
      const raw=await c.client.callTool({name:'list_targets',arguments:{discoverUsb:false}});
      if(!raw.isError){ready=true;break;}
    }
    assert.ok(ready,'Worker did not recover after cancellation');
    assert.throws(()=>process.kill(-group,0),e=>e.code==='ESRCH','Cancelled worker process group survived');
    const retained=await call(c,'read_ui_tree',{snapshotId:p.snapshotId,limit:1});
    assert.equal(retained.result.snapshotId,p.snapshotId);
    evidence.cancellation={busy:busy.error,workerGroupRemoved:true,unrelatedPhoneHandlePreserved:true};
    console.log('MCP cancellation removed the persistent worker/SSH group');
    // Simulate a stuck controller C call without freezing the phone: stop only
    // our idle Python worker. EOF must still reap its whole detached group
    // before the client's own shutdown grace forcibly kills the Node server.
    const forced=await connect();let forcedGroup;
    try {
      await call(forced,'get_status',phone);
      const lines=execFileSync('/bin/ps',['-axo','pid=,ppid=,pgid=,command='],{encoding:'utf8'});
      const stuck=lines.split('\n').map(s=>s.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(.+)$/)).find(m=>m && +m[2]===forced.transport.pid && m[4].includes('/mcp/backend.py'));
      assert.ok(stuck);forcedGroup=+stuck[3];
      process.kill(+stuck[1],'SIGSTOP');
      const stopStart=Date.now();await forced.close();
      const elapsed=(Date.now()-stopStart)/1000;
      assert.throws(()=>process.kill(-forcedGroup,0),e=>e.code==='ESRCH','Frozen worker group survived client EOF');
      assert.ok(elapsed<3.5,`Forced cleanup took ${elapsed}s`);
      evidence.forcedCleanup={simulatedStuckController:true,workerGroupRemoved:true,seconds:elapsed};
      console.log('MCP EOF reaped a deliberately stopped USB worker');
    } finally {
      if(forcedGroup)try{process.kill(-forcedGroup,'SIGKILL');}catch{}
      await forced.close();
    }
    const seconds=31-(Date.now()-start)/1000;
    if(seconds>0) await delay(seconds*1000);
    const expired=await call(expiration,'read_ui_tree',{snapshotId:exp.snapshotId},true);
    assert.match(expired.error,/expired/);evidence.expiry=expired.error;
  } finally {await expiration.close();}
  const fresh=(await call(c,'get_ui_tree',phone)).result;
  assert.equal(fresh.systemContext.frontmostBundleId,'org.openrevive.TouchProbe');
  await image(c,phone,'iphone-multitouch');
  evidence.timings=c.timings;
  await record('workflows',evidence);
} finally {
  await record('workflows-progress',evidence);
  try {await call(c,'run_command',{target:'mac',argv:['/bin/rm','-rf',remote]});} finally {await c.close();await rm(local,{recursive:true,force:true});}
}
console.log('OPENREVIVE_MCP_WORKFLOWS_OK');
