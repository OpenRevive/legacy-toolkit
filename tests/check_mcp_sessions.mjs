import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {BackendPool} from '../plugins/openrevive/mcp/src/backend-pool.mjs';
import {fileURLToPath} from 'node:url';

let created = 0;
class Worker {
  constructor() { this.id = ++created; this.child = {}; this.stops = 0; }
  async call(method, args, signal) {
    if (this.pending) throw new Error('Device operation in progress');
    if (signal.aborted) throw new Error('Cancelled before dispatch');
    this.pending = true;
    try {
      if (args.hold) return await new Promise((resolve,reject)=>{
        this.release = ()=>resolve({result:{worker:this.id}});
        signal.addEventListener('abort',()=>{
          this.child = undefined; this.stops++; reject(new Error('Cancelled; input may already have been posted'));
        },{once:true});
      });
      const result = {worker:this.id, method};
      if (method === 'observe_ui') result.snapshotId = this.id.toString(16).padStart(32,'0');
      if (method === 'act_and_wait') result.observation = {snapshotId:(100+this.id).toString(16).padStart(32,'0')};
      return {result};
    } finally { this.pending = false; }
  }
  async stop() { this.stops++; this.child = undefined; }
}
const pool = new BackendPool(()=>new Worker());
const a = 'a'.repeat(40), b = 'b'.repeat(40);
const signal = new AbortController().signal;
const initial = await pool.call('observe_ui',{host:'lion',target:'iphone',udid:a},signal);
const found = await pool.call('find_ui',{snapshotId:initial.result.snapshotId,label:'Apply'},signal);
assert.equal(found.result.worker,initial.result.worker);
const compound = await pool.call('act_and_wait',{snapshotId:initial.result.snapshotId,nodeId:'Apply'},signal);
assert.equal((await pool.call('read_ui_tree',{snapshotId:compound.result.observation.snapshotId},signal)).result.worker,initial.result.worker);
await assert.rejects(pool.call('act_ui',{snapshotId:'f'.repeat(32),nodeId:'unknown'},signal),/Unknown/);

// Telemetry may run while this device's control worker is busy. Input is never
// queued; another device can progress and cancelling A must not close B.
const cancel = new AbortController();
const held = pool.call('act_and_wait',{snapshotId:initial.result.snapshotId,hold:true},cancel.signal);
const rejected = assert.rejects(held,/Cancelled/);
const administrative = await pool.call('device_operation',{host:'lion',udid:a,action:'health'},signal);
assert.notEqual(administrative.result.worker,initial.result.worker);
const frame = await pool.call('get_screenshot',{host:'lion',target:'iphone',udid:a},signal);
assert.notEqual(frame.result.worker,initial.result.worker);
assert.notEqual(frame.result.worker,administrative.result.worker);
await assert.rejects(pool.call('observe_ui',{host:'lion',target:'iphone',udid:a},signal),/progress/);
const logs = await pool.call('read_logs',{host:'lion',udid:a},signal);
assert.notEqual(logs.result.worker,initial.result.worker);
const other = await pool.call('observe_ui',{host:'lion',target:'iphone',udid:b},signal);
await assert.rejects(pool.call('run_workflow',{host:'lion',arguments:['doctor']},signal),/progress/);
cancel.abort(); await rejected;
await assert.rejects(pool.call('find_ui',{snapshotId:initial.result.snapshotId,label:'Apply'},signal),/Unknown/);
assert.equal((await pool.call('find_ui',{snapshotId:other.result.snapshotId,label:'Apply'},signal)).result.worker,other.result.worker);

const telemetry = [...pool.workers.values()].find(w=>w.id===logs.result.worker);
await pool.call('device_session',{host:'lion',udid:a,operation:'close',component:'all'},signal);
assert.equal(telemetry.stops,1);
assert.equal((await pool.call('find_ui',{snapshotId:other.result.snapshotId,label:'Apply'},signal)).result.worker,other.result.worker);

// A host workflow invalidates its handles for its entire lifetime; other hosts
// retain independent state, and a busy worker is not killed to make room.
const remote = await pool.call('observe_ui',{host:'other-lion',target:'iphone',udid:a},signal);
const workflow = pool.call('run_workflow',{host:'lion',arguments:['doctor'],hold:true},signal);
await new Promise(resolve=>setImmediate(resolve));
await assert.rejects(pool.call('observe_ui',{host:'lion',target:'iphone',udid:b},signal),/workflow/);
assert.equal((await pool.call('find_ui',{snapshotId:remote.result.snapshotId,label:'Apply'},signal)).result.worker,remote.result.worker);
const workflowWorker = [...pool.workers.values()].find(w=>w.host==='lion'&&w.pending);
workflowWorker.release(); await workflow;
await assert.rejects(pool.call('read_ui_tree',{snapshotId:other.result.snapshotId},signal),/Unknown/);
await pool.stop(); assert.equal(pool.workers.size,0); assert.equal(pool.handles.size,0);

// Real bundled stdio server: JSON schemas, new typed tools, protocol validation
// and the old 2025-03-26 client still exercise the actual Python worker.
execFileSync(process.execPath,[fileURLToPath(new URL('./check_mcp_protocol.mjs',import.meta.url))],{stdio:'inherit'});
console.log('OPENREVIVE_MCP_SESSIONS_OK');
