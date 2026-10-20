import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import { connect,call,record,ROOT } from './mcp_client.mjs';
const fixture=mkdtempSync(tmpdir()+'/openrevive-mcp-protocol-');
process.once('exit',()=>rmSync(fixture,{recursive:true,force:true}));
const config=fixture+'/hosts.json';
const fixtureEnv={...process.env,OPENREVIVE_CONFIG:config,OPENREVIVE_SUPERVISOR_ROOT:fixture+'/service'};
const a=await connect(undefined,'/tmp',{env:fixtureEnv});
try {
  const tools=await a.client.listTools();
  assert.equal(tools.tools.length,32);
  for(const t of tools.tools) {
    assert.equal(t.inputSchema.type,'object',t.name);
    assert.ok(t.outputSchema,t.name);
    assert.equal(t.inputSchema.additionalProperties,false,t.name);
  }
  const resources=await a.client.listResources();
  const actionTool=tools.tools.find(t=>t.name==='act_ui');
  assert.ok(actionTool.inputSchema.properties.action.enum.includes('end-editing'));
  for(const name of ['device_session','get_capabilities','observe_ui','act_and_wait','read_logs','list_crashes','read_crash','capture_network','web_inspect'])assert.ok(tools.tools.some(t=>t.name===name),name);
  assert.equal(resources.resources.length,2);
  const guide=await a.client.readResource({uri:'openrevive://guide'});
  assert.match(guide.contents[0].text,/30 seconds/);
  const missingConfig=await call(a,'list_targets',{discoverUsb:false});
  assert.deepEqual(missingConfig.result.hosts,[]);assert.match(missingConfig.result.setup,/init/);
  writeFileSync(config,JSON.stringify({version:1,hosts:{blacklion:{address:'127.0.0.1',user:'fixture'}}}));
  const targets=await call(a,'list_targets',{discoverUsb:false});
  assert.ok(targets.result.hosts.some(h=>h.host==='blacklion'));
  assert.ok(!JSON.stringify(targets).includes('identity'));
  for(const [name,args] of [
    ['get_status',{target:'iphone'}],['get_ui_tree',{target:'mac',depth:21}],
    ['run_command',{target:'mac',argv:[]}],['list_targets',{bogus:true}],
    ['mac_input',{expectedPid:1,operation:'click'}],
    ['iphone_gesture',{snapshotId:'a'.repeat(32),gesture:{fingers:[]}}],
    ['run_command',{target:'mac',argv:['x\0y']}],
    ['act_and_wait',{snapshotId:'a'.repeat(32),nodeId:'x',expect:{state:'absent',selector:{}}}],
    ['capture_network',{udid:'a'.repeat(40),duration:21}],
    ['web_inspect',{udid:'a'.repeat(40),operation:'evaluate'}],
  ]) {
    // SDK argument errors may be returned as tool errors or protocol errors.
    let rejected=false;
    try { const r=await a.client.callTool({name,arguments:args}); rejected=!!r.isError; } catch {rejected=true;}
    assert.ok(rejected,JSON.stringify([name,args]));
  }
  const missing=await call(a,'read_ui_tree',{snapshotId:'0'.repeat(32)},true);
  assert.match(missing.error,/Unknown/);
  const tooLong=await call(a,'run_command',{target:'iphone',udid:'a'.repeat(40),argv:['/bin/echo','never dispatched'],timeout:301},true);
  assert.match(tooLong.error,/1\.\.300/);
  await record('protocol',{server:a.client.getServerVersion(),tools:tools.tools.map(t=>t.name),resources:resources.resources,invalidArgumentsRejected:10,unknownSnapshot:missing.error});
  assert.equal(a.errors(),'');
} finally {await a.close();}
// An older installed client must work; EOF must clean up a started worker.
const child=spawn(process.execPath,[ROOT+'plugins/openrevive/mcp/server.mjs'],{cwd:'/tmp',stdio:['pipe','pipe','pipe'],env:fixtureEnv});
let buffer='',stderr='';const messages=[];
child.stdout.setEncoding('utf8');child.stdout.on('data',data=>{
  buffer+=data;
  while(buffer.includes('\n')) {const i=buffer.indexOf('\n');messages.push(JSON.parse(buffer.slice(0,i)));buffer=buffer.slice(i+1);}
});
child.stderr.on('data',data=>{stderr+=data.toString();});
const send=value=>child.stdin.write(JSON.stringify(value)+'\n');
async function reply(id) {
  const end=Date.now()+10000;
  while(Date.now()<end) {const m=messages.find(m=>m.id===id);if(m)return m;await new Promise(r=>setTimeout(r,10));}
  throw new Error('Legacy MCP response timed out');
}
try {
  send({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'legacy-stdio',version:'1'}}});
  assert.equal((await reply(1)).result.protocolVersion,'2025-03-26');
  send({jsonrpc:'2.0',method:'notifications/initialized'});
  send({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:'list_targets',arguments:{discoverUsb:false}}});
  assert.ok(!(await reply(2)).result.isError);
  const exit=once(child,'exit');
  child.stdin.end();
  const timer=setTimeout(()=>child.kill('SIGKILL'),10000);
  const [code,signal]=await exit;clearTimeout(timer);
  assert.equal(code,0,stderr);assert.equal(signal,null,stderr);assert.equal(stderr,'');
} finally {if(child.exitCode===null)child.kill('SIGKILL');}
const missing=await connect(undefined,'/tmp',{env:{...fixtureEnv,OPENREVIVE_PYTHON:'/no-such-openrevive-python'}});
try {
  const failure=await call(missing,'list_targets',{discoverUsb:false},true);
  assert.match(failure.error,/Cannot start Python worker/);
} finally {await missing.close();}
execFileSync('python3',[ROOT+'plugins/openrevive/mcp/supervisor.py','--shutdown'],{env:fixtureEnv});
console.log('OPENREVIVE_MCP_PROTOCOL_OK');
