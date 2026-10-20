import assert from 'node:assert/strict';
import {connect,call,image,select,record,UDID} from './mcp_client.mjs';
const target=process.argv[2];
assert.ok(['mac','iphone'].includes(target));
if(target==='iphone') assert.ok(UDID,'--udid is required');
const c=await connect();
const loc={target,...(UDID?{udid:UDID}:{})};
const results={};
const snapshot=async (extra={}) => (await call(c,'get_ui_tree',{...loc,...extra})).result;
try {
  if(target==='mac') {
    const {result:status}=await call(c,'get_status',loc);
    assert.ok(status.ready);
    const remote=status.prefix+'/current';
    await call(c,'run_command',{target:'mac',argv:['/usr/bin/open',remote+'/OpenReviveProbe.app']});
    const apps=(await call(c,'list_mac_apps')).result.items;
    const probe=apps.find(a=>a.bundleId==='org.openrevive.Probe');
    assert.ok(probe,JSON.stringify(apps));
    await call(c,'mac_input',{expectedPid:probe.pid,operation:'focus'});
    let tree=await snapshot({pid:probe.pid});
    const field=await select(c,tree,{role:'AXTextField'});
    const text='MCP Mac: Привет 日本語 '+Date.now();
    results.set=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:field.id,action:'set-text',value:text})).result;
    results.consumed=await call(c,'read_ui_tree',{snapshotId:tree.snapshotId},true);
    tree=await snapshot({pid:probe.pid});
    const button=await select(c,tree,{label:'Apply probe',role:'AXButton'});
    results.press=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:button.id})).result;
    const receipt=(await call(c,'read_file',{target:'mac',path:'~/Library/Logs/OpenReviveProbe/result.json'})).result;
    results.receipt=JSON.parse(receipt.data);
    assert.equal(results.receipt.text,text);
    assert.ok(results.receipt.visibleResult.includes(text));
    results.screen=await image(c,loc,'mac-form');
    assert.deepEqual([results.screen.width,results.screen.height],[1280,800]);
    const fresh=await snapshot({pid:probe.pid});
    assert.ok(fresh.nodes.some(n=>n.value===text));
    results.tree=fresh;
    const other=await connect();
    try {results.sessionIsolation=await call(other,'read_ui_tree',{snapshotId:fresh.snapshotId},true);}
    finally {await other.close();}
    assert.match(results.sessionIsolation.error,/Unknown/);
  } else {
    await call(c,'iphone_input',{udid:UDID,operation:'launch',bundle:'org.openrevive.TouchProbe'});
    let tree=await snapshot();
    // If AssistiveTouch was already open, close it through its actual Home button.
    let menu=(await call(c,'find_ui',{snapshotId:tree.snapshotId,className:'HNDRockerButton',label:'Домой',allMatches:true})).result;
    if(menu.count===1) {
      await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:menu.selected.id});
      await call(c,'iphone_input',{udid:UDID,operation:'launch',bundle:'org.openrevive.TouchProbe'});
      tree=await snapshot();
    }
    results.coverage=tree.coverage;
    const field=await select(c,tree,{label:'Probe text',role:'text-field',owner:'org.openrevive.TouchProbe'});
    const text='MCP iPhone: Привет 日本語 '+Date.now();
    results.set=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:field.id,action:'set-text',value:text})).result;
    tree=await snapshot();
    const button=await select(c,tree,{label:'Apply probe',role:'button',owner:'org.openrevive.TouchProbe'});
    results.press=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:button.id})).result;
    tree=await snapshot();
    const app=tree.owners.find(o=>o.bundleId==='org.openrevive.TouchProbe');
    assert.ok(app?.containerPath,JSON.stringify(tree.owners));
    const ownerPid=app.pid;
    const container=app.containerPath;
    const receipt=(await call(c,'read_file',{...loc,path:container+'/Documents/probe-result.json',maxBytes:100000})).result;
    results.receipt=JSON.parse(receipt.data);
    assert.equal(results.receipt.text,text);
    assert.ok(results.receipt.visibleResult.includes(text));
    results.screen=await image(c,loc,'iphone-form');
    assert.deepEqual([results.screen.width,results.screen.height],[640,960]);
    tree=await snapshot();
    const rocker=await select(c,tree,{label:'Меню AssistiveTouch',className:'HNDRocker'});
    results.assistiveOpen=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:rocker.id})).result;
    assert.equal(results.assistiveOpen.route,'vnc');
    await image(c,loc,'iphone-assistive-menu');
    tree=await snapshot();
    const home=await select(c,tree,{label:'Домой',className:'HNDRockerButton'});
    results.assistiveHome=(await call(c,'act_ui',{snapshotId:tree.snapshotId,nodeId:home.id})).result;
    assert.equal(results.assistiveHome.route,'vnc');
    const state=(await snapshot()).systemContext;
    assert.equal(state.frontmostBundleId,'',JSON.stringify(state));
    assert.equal(state.surface,'home',JSON.stringify(state));
    results.homeState=state;
    await image(c,loc,'iphone-home');
    await call(c,'iphone_input',{udid:UDID,operation:'launch',bundle:'org.openrevive.TouchProbe'});
    results.ownerPid=ownerPid;
  }
  await record('live-'+target,results);
  assert.equal(c.errors(),'');
} finally {await c.close();}
console.log('OPENREVIVE_MCP_'+target.toUpperCase()+'_OK');
