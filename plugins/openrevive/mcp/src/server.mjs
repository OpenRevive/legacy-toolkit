#!/usr/bin/env node
import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { z } from 'zod';
import { spawn } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { BackendPool } from './backend-pool.mjs';
import { randomUUID } from 'node:crypto';

const VERSION = '0.8.0';
if (Number(process.versions.node.split('.')[0]) < 20) {
  console.error('OpenRevive MCP requires Node.js 20+ on the controlling Mac.');
  process.exit(2);
}
const base = existsSync(new URL('./backend.py', import.meta.url)) ? new URL('./', import.meta.url) : new URL('../', import.meta.url);
const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i];
  if (!['--host', '--config', '--python'].includes(key) || !process.argv[i+1]) {
    console.error('Usage: node server.mjs [--host PROFILE] [--config HOSTS_JSON] [--python PYTHON3]');
    process.exit(2);
  }
  options[key.slice(2)] = process.argv[i+1];
}
const defaultHost = options.host || process.env.OPENREVIVE_HOST || 'blacklion';
const python = options.python || process.env.OPENREVIVE_PYTHON || 'python3';
const env = {...process.env, ...(options.config ? {OPENREVIVE_CONFIG: options.config} : {}), PYTHONUNBUFFERED: '1'};
const consumerId = randomUUID().replaceAll('-', '');

// Each worker owns its USB process-global C state. Never queue input behind
// a slow request: a queued action might execute after its observed UI changed.
class Backend {
  child;
  pending;
  stopping;
  start() {
    if (this.child) return;
    const child = spawn(python, [fileURLToPath(new URL('supervisor.py', base)), '--client'], {
      env: {...env, OPENREVIVE_CONSUMER_ID: consumerId,
        OPENREVIVE_SUPERVISOR_ROUTE: JSON.stringify({host:this.host,udid:this.udid})},
      stdio: ['pipe', 'pipe', 'pipe'], detached: true,
    });
    this.child = child;
    let buffer = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', data => {
      if (this.child !== child) return;
      buffer += data;
      if (Buffer.byteLength(buffer) > 16*1024*1024) {
        this.pending?.reject(new Error('Worker response exceeded 16 MiB'));
        void this.stop(); return;
      }
      const index = buffer.indexOf('\n');
      if (index < 0) return;
      const line = buffer.slice(0, index); buffer = buffer.slice(index+1);
      try { this.pending?.resolve(JSON.parse(line)); }
      catch { this.pending?.reject(new Error('Invalid worker response')); void this.stop(); }
    });
    child.stderr.on('data', data => process.stderr.write(data.subarray(0, 4000)));
    child.on('error', error => {
      if (this.child !== child) return;
      this.child = undefined;
      this.pending?.reject(new Error(`Cannot start Python worker: ${error.message}. Set OPENREVIVE_PYTHON to Python 3.9+.`));
    });
    child.on('exit', (code, signal) => {
      if (this.child !== child) return;
      this.child = undefined;
      this.pending?.reject(new Error(`Worker exited (${signal || code}); its device snapshot IDs are invalid. Read a fresh tree.`));
    });
    child.stdin.on('error', error => { if (this.child === child) this.pending?.reject(error); });
  }
  async call(method, args, signal) {
    if (this.pending || this.stopping) throw new Error('Device operation in progress; wait for it to finish, then refresh the tree before input.');
    if (signal.aborted) throw new Error('Request cancelled before dispatch');
    const payload = JSON.stringify({method, args});
    if (Buffer.byteLength(payload) > 200000) throw new Error('Request exceeds 200 KB');
    this.start();
    const deadline = (['run_command', 'run_workflow', 'ui_perform', 'ui_workflow', 'ui_contexts', 'ui_observe'].includes(method) ? (args.timeout || (method === 'run_workflow' ? 600 : 120))+30 : 90)*1000;
    let timer;
    let onAbort;
    try {
      return await new Promise((resolve, reject) => {
        this.pending = {resolve, reject};
        onAbort = () => { reject(new Error('Request cancelled. Snapshots are invalidated; already posted input cannot be undone. Inspect the device before retrying.')); void this.stop(); };
        signal.addEventListener('abort', onAbort, {once:true});
        timer = setTimeout(() => { reject(new Error('Operation deadline exceeded; inspect device state before retrying.')); void this.stop(); }, deadline);
        this.child.stdin.write(payload+'\n');
      });
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      this.pending = undefined;
    }
  }
  async stop() {
    if (this.stopping) return this.stopping;
    const child = this.child;
    if (!child) return;
    this.stopping = new Promise(resolve => {
      const timer = setTimeout(() => {
        try { process.kill(-child.pid, 'SIGKILL'); } catch {}
      // Finish before clients' stdio shutdown grace expires. A stuck ctypes
      // call must not leave the detached USB worker behind when Node is killed.
      }, 1500);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
      // The group also contains SSH and command workers. Python's handler
      // unwinds the tunnel's finally block; SIGKILL is a bounded last resort.
      try { process.kill(-child.pid, 'SIGTERM'); } catch { clearTimeout(timer); resolve(); }
    });
    try { await this.stopping; } finally { this.stopping = undefined; }
  }
}
const backend = new BackendPool(() => new Backend(), defaultHost);
const str = z.string().max(16384).refine(v => !v.includes('\0'), 'NUL is not allowed');
const short = str.pipe(z.string().max(500));
const host = z.string().regex(/^[A-Za-z0-9_.-]{1,80}$/).default(defaultHost);
const udid = z.string().regex(/^[0-9a-fA-F-]{24,64}$/).optional();
const target = z.enum(['mac','iphone']);
const handle = z.string().regex(/^[a-f0-9]{32}$/);
const pid = z.number().int().min(1);
const location = {host, target, udid};
const located = schema => z.strictObject(schema).refine(a => a.target !== 'iphone' || !!a.udid, 'iPhone tools require an exact UDID from list_targets');
const outputSchema = z.object({ok:z.boolean(), result:z.record(z.string(),z.unknown()).optional(), error:z.string().optional()});
const deviceLocation = {host,udid:udid.unwrap()};
const selector = z.strictObject({label:short.optional(),contains:short.optional(),role:short.optional(),
  owner:short.optional(),identifier:short.optional(),className:short.optional()});
const nonemptySelector = selector.refine(a=>Object.keys(a).length>0,'Supply at least one selector');
const uiValue = z.union([str,z.boolean(),z.number().finite(),z.tuple([z.number().finite(),z.number().finite()])]);
const uiAction = z.enum(['auto','press','set-text','end-editing','return-key','set-on','set-value','scroll','focus','activate','tap']);
const postcondition = z.discriminatedUnion('state',[
  z.strictObject({state:z.literal('present'),selector:nonemptySelector}),
  z.strictObject({state:z.literal('absent'),selector:nonemptySelector}),
  z.strictObject({state:z.literal('value'),selector:nonemptySelector,value:uiValue}),
  z.strictObject({state:z.literal('foreground'),bundle:short}),
  z.strictObject({state:z.literal('changed')}),
]);
function scopedSelector(depth=0) {
  return z.strictObject({label:short.optional(),identifier:short.optional(),role:short.optional(),
    owner:short.optional(),className:short.optional(),...(depth<3?{within:scopedSelector(depth+1).optional()}: {})})
    .refine(a=>Object.keys(a).length>0,'Supply an exact selector');
}
const scopedTarget=scopedSelector();
const condition=z.discriminatedUnion('state',[
  z.strictObject({state:z.literal('present'),selector:scopedTarget}),
  z.strictObject({state:z.literal('absent'),selector:scopedTarget}),
  z.strictObject({state:z.literal('value'),selector:scopedTarget,value:uiValue,
    attribute:z.enum(['value','isOn','enabled','visible','isFirstResponder','contentOffsetPoints']).optional()}),
  z.strictObject({state:z.literal('foreground'),bundle:short}),
]);
const presentation={nodeLimit:z.number().int().min(1).max(150).default(40),byteLimit:z.number().int().min(2048).max(65536).default(12288)};
const performFields={selector:scopedTarget,action:uiAction.default('auto'),value:uiValue.optional(),expect:condition,
  mode:z.enum(['perform','ensure']).default('perform'),transition:z.enum(['becomes_true','any']).default('becomes_true'),
  stableMs:z.number().int().min(0).max(2000).default(100)};
function workflowStep(depth=0) {
  const cases=[
    z.strictObject({kind:z.literal('perform'),...performFields}),
    z.strictObject({kind:z.literal('observe'),...presentation}),
    z.strictObject({kind:z.literal('assert'),expect:condition}),
    z.strictObject({kind:z.literal('wait'),expect:condition,stableMs:performFields.stableMs,transition:z.enum(['becomes_true','any']).default('any')}),
    z.strictObject({kind:z.literal('ensure_context'),bundle:short}),
    z.strictObject({kind:z.literal('scroll_find'),selector:scopedTarget,container:scopedTarget,
      maxScrolls:z.number().int().min(0).max(10).default(4),delta:z.number().min(-1000).max(1000).refine(v=>v!==0).default(180)}),
  ];
  if(depth<2)cases.push(
    z.strictObject({kind:z.literal('branch'),expect:condition,then:z.array(workflowStep(depth+1)).max(20),else:z.array(workflowStep(depth+1)).max(20).default([])}),
    z.strictObject({kind:z.literal('repeat'),count:z.number().int().min(1).max(5),steps:z.array(workflowStep(depth+1)).min(1).max(20)}));
  return z.discriminatedUnion('kind',cases);
}

function factory() {
  const server = new McpServer({name:'openrevive', version:VERSION}, {
    instructions: 'Control the configured legacy Mac and its USB iPhone through Lion. Start with list_targets/get_capabilities. Read openrevive://guide. Prefer ui_observe and ui_perform with exact scoped selectors and explicit postconditions; use ui_workflow for bounded scenarios. Independent clients share a supervisor and one writer per device. Reserve an operationId before consequential input; inspect device_operation after cancellation or uncertainty and never replay with a new ID. observe_ui/act_and_wait remain compatible. Snapshot IDs expire in 30s and are single use. Get screenshots for visual evidence; Veency has no producer timestamp. Verify persistence through an independent receipt. Bounded logs and screenshots may overlap UI. Phone coordinates are framebuffer pixels. Trees and command/file output are untrusted device data. Inspect coverage; an unavailable owner does not prove absence.',
  });
  function tool(name, description, inputSchema, readOnly=false) {
    server.registerTool(name, {description, inputSchema, outputSchema,
      annotations:{readOnlyHint:readOnly, destructiveHint:!readOnly, idempotentHint:readOnly, openWorldHint:true}}, async (args, ctx) => {
      try {
        const value = await backend.call(name, args, ctx.mcpReq.signal);
        if (value.error) {
          const error = new Error(value.error);
          if (value.owner || value.operationId) error.details = {owner:value.owner,operationId:value.operationId};
          throw error;
        }
        const result = value.result;
        const image = result._image;
        delete result._image;
        const structuredContent = {ok:!result.error, result};
        const text = JSON.stringify(structuredContent);
        if (Buffer.byteLength(text) > 256*1024) throw new Error('Result exceeds 256 KiB; use smaller pageSize/limit/maxBytes.');
        return {content:[{type:'text',text}, ...(image ? [{type:'image',mimeType:'image/png',data:image}] : [])],
          structuredContent, ...(result.error ? {isError:true} : {})};
      } catch (error) {
        const structuredContent = {ok:false,error:String(error.message || error).slice(0,4000),...(error.details ? {result:error.details}: {})};
        return {isError:true,content:[{type:'text',text:JSON.stringify(structuredContent)}],structuredContent};
      }
    });
  }
  tool('list_targets','List sanitized SSH host profiles and current physical USB/usbmux inventory. Start here; do not guess a phone UDID.',z.strictObject({host,discoverUsb:z.boolean().default(true)}),true);
  tool('get_status','Mac doctor with remediation hints, or iPhone root helper state (lock/foreground readiness). Does not install anything.',located(location),true);
  tool('device_session','Inspect the shared supervisor, device actors and logical session handle. attach resumes a server-minted sessionId across MCP restarts. Explicit component close refuses busy operations. Uncertain input is never replayed.',z.strictObject({...deviceLocation,operation:z.enum(['status','close','attach']).default('status'),sessionId:handle.optional(),component:z.enum(['all','transport','command','vnc','web','logs','crashes']).default('all')}).refine(a=>a.operation!=='attach'||!!a.sessionId,'attach requires sessionId'));
  tool('ui_observe','Read a fresh compact iPhone observation with exact byte/node budgets and per-consumer diff bases. Missing bases resynchronize. cached returns historical data without interrupting a writer; cached coordinates never authorize input.',z.strictObject({...deviceLocation,...presentation,since:handle.optional(),cached:z.boolean().default(false),timeout:z.number().min(.1).max(120).default(30)}),true);
  tool('ui_perform','Resolve one exact scoped selector against fresh native state, dispatch once through existing owner/process/overlay/layout guards, and verify a named condition locally. bundle is the required foreground app. ensure skips input if already satisfied. Total budget includes observation and dispatch; an existing bridge call can overrun it. No retries or mechanism fallback.',z.strictObject({...deviceLocation,...performFields,...presentation,bundle:short,
    timeout:z.number().min(.1).max(120).default(30),maxDispatches:z.literal(1).default(1),operationId:handle.optional()}));
  tool('ui_workflow','Execute a small bounded native UI scenario: assert, observe, ensure_context, perform, wait, scroll_find, branch and repeat. One writer is held through verification. Returns completed/failing steps, cumulative effects and final observation. No automatic action replay.',z.strictObject({...deviceLocation,bundle:short.optional(),steps:z.array(workflowStep()).min(1).max(20),...presentation,
    timeout:z.number().min(.1).max(120).default(60),maxDispatches:z.number().int().min(1).max(30).default(12),operationId:handle.optional()}));
  tool('ui_contexts','Discover native owners and iOS 6 web pages with their actual guard/reference scopes. Logical discovery does not launch an app or activate a page.',z.strictObject({...deviceLocation,includeWeb:z.boolean().default(true),...presentation,timeout:z.number().min(.1).max(120).default(30)}),true);
  tool('device_operation','Reserve/query durable operation identities, inspect traces, request cancellation, reconcile execution or hand control to a person. Cancellation request is distinct from confirmed quiescence. Reconciliation never replays input. After manual control, resume requires fresh observations.',z.strictObject({...deviceLocation,
    action:z.enum(['reserve','status','list','cancel','trace','health','handover','resume','reconcile']).default('status'),
    operationId:handle.optional(),limit:z.number().int().min(1).max(100).default(20)})
    .refine(a=>!['status','cancel','trace','reconcile'].includes(a.action)||!!a.operationId,'This action requires operationId'));
  tool('device_artifact','Read a private content-addressed screenshot artifact returned by this logical session. Images are separate from observation JSON; fetching never sends device input.',z.strictObject({...deviceLocation,artifactId:z.string().regex(/^[a-f0-9]{64}$/)}),true);
  tool('get_capabilities','Read actual device identity and probe selected existing services with individual status, evidence and remedies. No repairs, installs, image mounts or direct controller USB. Service availability does not prove UI/input or packet-decoder acceptance.',z.strictObject({...deviceLocation,checks:z.array(z.enum(['command','uikit','hid','vnc','screenshot','web','logs','crashes','network'])).max(9).default(['command','uikit','hid','vnc','screenshot','web','logs','crashes','network'])}),true);
  tool('list_mac_apps','List real running Mac applications and their PID/bundle identifiers.',z.strictObject({host}),true);
  tool('list_mac_windows','List real Mac windows, owners and desktop frames.',z.strictObject({host}),true);
  tool('get_ui_tree','Capture Mac AX tree (PID, defaults to foreground) or unified iPhone tree including SpringBoard/AssistiveTouch. Returns a private 30-second single-use snapshotId, a first node page, geometry and coverage. A new tree supersedes older snapshots of that device.',located({...location,pid:pid.optional(),depth:z.number().int().min(1).max(20).default(16),limit:z.number().int().min(1).max(1500).default(1000),pageSize:z.number().int().min(1).max(150).default(80),includeHidden:z.boolean().default(false)}),true);
  tool('observe_ui','Capture a fresh guarded UI snapshot, returning compact visible labelled/actionable nodes and coverage. Optional since returns a bounded diff against this device observation revision. Full native guards remain private; read_ui_tree pages the complete snapshot. A new observation supersedes earlier actionable snapshots.',located({...location,pid:pid.optional(),depth:z.number().int().min(1).max(20).default(16),limit:z.number().int().min(1).max(1500).default(1000),nodeLimit:z.number().int().min(1).max(150).default(40),selector:selector.optional(),since:handle.optional(),includeHidden:z.boolean().default(false)}),true);
  tool('act_and_wait','Consume one fresh snapshot and dispatch its node action exactly once, then poll fresh UI until an explicit postcondition or timeout. Returns action receipt, verification and a compact fresh observation. Timeout/uncertain acknowledgement never repeats input. UI success does not prove persisted server-side effects.',z.strictObject({snapshotId:handle,nodeId:short,action:uiAction.default('auto'),value:uiValue.optional(),expect:postcondition,timeout:z.number().min(0).max(20).default(6),interval:z.number().min(.1).max(2).default(.35),nodeLimit:z.number().int().min(1).max(150).default(40)}));
  tool('read_ui_tree','Read another page of an existing snapshot without remote I/O. Does not refresh its expiry.',z.strictObject({snapshotId:handle,offset:z.number().int().min(0).default(0),limit:z.number().int().min(1).max(150).default(100)}),true);
  tool('find_ui','Search the entire cached tree locally. Exact label/role/owner/identifier/class or case-insensitive contains. By default require one actionable match. Refine an ambiguous result; never guess its nodeId.',z.strictObject({snapshotId:handle,label:short.optional(),contains:short.optional(),role:short.optional(),owner:short.optional(),identifier:short.optional(),className:short.optional(),allMatches:z.boolean().default(false),includeUnavailable:z.boolean().default(false),limit:z.number().int().min(1).max(100).default(40)}),true);
  tool('act_ui','Consume a snapshot and act on its actual nodeId. auto chooses verified semantic/HID/VNC route on iPhone, or AXPress on Mac. iPhone set-text focuses and replaces through UIKit input; end-editing lets the app commit through its normal editing lifecycle. Read a fresh tree between actions. Other typed edits: set-on boolean, set-value number, scroll [x,y]. Native ownership/geometry/freshness guards are preserved; failures never fall back to an unguarded click. Mac supports press/set-text. Verify saved app state.',z.strictObject({snapshotId:handle,nodeId:short,action:z.enum(['auto','press','set-text','end-editing','return-key','set-on','set-value','scroll','focus','activate','tap']).default('auto'),value:z.union([str,z.boolean(),z.number().finite(),z.tuple([z.number().finite(),z.number().finite()])]).optional()}));
  tool('get_screenshot','Return PNG as an inline image and private artifact handle. Native screenshotr includes system overlays. Persistent VNC uses incremental updates after warmup; no update returns explicitly cached pixels with the old acquisition time. incremental:false requests a full frame. crop is [x,y,width,height] in full-screen pixels; it reduces returned image size, not necessarily device traffic.',located({...location,transport:z.enum(['native','vnc']).default('native'),incremental:z.boolean().default(true),updateTimeout:z.number().min(.1).max(5).default(.75),
    crop:z.tuple([z.number().int().min(0),z.number().int().min(0),z.number().int().min(1),z.number().int().min(1)]).optional()}),true);
  tool('mac_input','Direct Mac desktop input in pixels. expectedPid must still be foreground; focus is an explicit operation. Prefer act_ui for AX controls. Read a screenshot before coordinate input; verify afterwards.',z.strictObject({host,expectedPid:pid,operation:z.enum(['focus','click','double-click','move','drag','scroll','type','key']),x:z.number().int().min(0).optional(),y:z.number().int().min(0).optional(),x2:z.number().int().min(0).optional(),y2:z.number().int().min(0).optional(),button:z.enum(['left','right']).optional(),text:str.optional(),key:short.optional(),modifiers:z.string().regex(/^(?:(?:cmd|shift|alt|ctrl)(?:,(?:cmd|shift|alt|ctrl))*)?$/).optional(),vertical:z.number().int().min(-100).max(100).optional(),horizontal:z.number().int().min(-100).max(100).optional()}).superRefine((a,c)=>{
    const required = {click:['x','y'],'double-click':['x','y'],move:['x','y'],drag:['x','y','x2','y2'],type:['text'],key:['key'],scroll:['vertical']}[a.operation] || [];
    for (const k of required) if (a[k]===undefined) c.addIssue({code:'custom',message:`${a.operation} requires ${k}`,path:[k]});
  }));
  tool('iphone_input','Explicit global iPhone Home/Lock, app launch, VNC keyboard text or named key. This invalidates snapshots. It intentionally targets the current screen; prefer act_ui for owner-bound edits. No passcode guessing.',z.strictObject({host,udid:udid.unwrap(),operation:z.enum(['home','lock','launch','type','key']),bundle:short.optional(),text:str.optional(),key:z.enum(['return','backspace','tab','escape','left','right','up','down']).optional()}).superRefine((a,c)=>{
    for (const k of ({launch:['bundle'],type:['text'],key:['key']}[a.operation] || [])) if (a[k]===undefined) c.addIssue({code:'custom',message:`${a.operation} requires ${k}`,path:[k]});
  }));
  const point = z.strictObject({t:z.number().min(0).max(5),x:z.number().min(0).lt(768),y:z.number().min(0).lt(1024)});
  tool('iphone_gesture','Real concurrent multitouch, 1..5 fingers, at most 5s, in native portrait screen pixels (iPhone 640×960; iPad 768×1024). The selected device validates its own bounds. Consumes a fresh unified snapshot. bundle binds to observed foreground app; omit for guarded SpringBoard. Dismiss external menus first. Native driver rechecks foreground and releases contacts on error.',z.strictObject({snapshotId:handle,bundle:short.optional(),gesture:z.strictObject({fps:z.number().int().min(10).max(60).optional(),fingers:z.array(z.strictObject({id:z.number().int().min(1).max(5),points:z.array(point).min(2).max(64)})).min(1).max(5)})}));
  tool('iphone_system_control','Consume a unified snapshot for guarded system navigation or passcode-free legacy unlock.',z.strictObject({snapshotId:handle,action:z.enum(['switcher','notifications','dismiss-notifications','dismiss-switcher','unlock'])}));
  tool('run_command','Run an explicit argv on the Mac (optional Aqua/cwd/env) or on the prepared test iPhone as root. No implicit shell. Timeout is 1..3600s on Mac, 1..300s on iPhone. Returns real exitCode, stdout/stderr and truncation flags; nonzero isError. Output capped at 64 KiB per stream. Cancellation cannot undo remote side effects; inspect before retrying.',located({...location,argv:z.array(str).min(1).max(128),cwd:str.optional(),env:z.record(z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/),str).optional(),aqua:z.boolean().default(false),timeout:z.number().int().min(1).max(3600).default(120)}));
  tool('sync_source','Incrementally upload a local source directory to the Mac via quoted rsync. Exclusions supported; never deletes remote-only files.',z.strictObject({host,localDirectory:str,remoteDirectory:str,exclude:z.array(short).max(100).default([])}));
  tool('read_file','Read a bounded remote file/independent test receipt. Mac user files or iPhone AFC2 files. utf8 or base64. Mac truncates with a flag; AFC rejects files exceeding maxBytes.',located({...location,path:str,maxBytes:z.number().int().min(1).max(100000).default(65536),encoding:z.enum(['utf8','base64']).default('utf8')}),true);
  tool('read_logs','Read a bounded live legacy syslog_relay stream with exact process name/PID and optional text filter. Persistent stream; no device package. Runs separately from UI, so logs can be collected during an action. Output may contain private application data.',z.strictObject({...deviceLocation,duration:z.number().min(.1).max(20).default(2),limit:z.number().int().min(1).max(300).default(80),process:short.optional(),contains:short.optional()}),true);
  tool('list_crashes','List existing mobile crash reports through AFC. Never starts the crash mover or deletes device reports.',z.strictObject({...deviceLocation,process:short.optional(),limit:z.number().int().min(1).max(150).default(80)}),true);
  tool('read_crash','Read one exact report name from list_crashes. Size bounded; oversized reports fail rather than silently truncate. Device report is preserved.',z.strictObject({...deviceLocation,name:short,maxBytes:z.number().int().min(1).max(100000).default(65536)}),true);
  tool('capture_network','Capture a bounded com.apple.pcapd stream over Lion USB into a private local PCAP artifact. Returns path/hash/counts and small metadata, never inline payloads. Optional process/interface filters require metadata present in that OS header. RAW IP output skips non-IP packets. Does not change DNS, proxy or TLS.',z.strictObject({...deviceLocation,duration:z.number().min(.1).max(20).default(3),limit:z.number().int().min(1).max(2000).default(200),process:short.optional(),interface:short.optional()}),true);
  tool('web_inspect','Inspect iOS 6 Safari/development UIWebView using the legacy WebKit protocol: pages, bounded DOM, explicit JavaScript evaluation, or Console/Network events. First list pages and use its opaque pageId. Does not steal another inspector connection. iOS 5 standard Web Inspector is unsupported. JavaScript can change page state; no automatic replay.',z.strictObject({...deviceLocation,operation:z.enum(['pages','context','dom','evaluate','events']),pageId:handle.optional(),contextId:handle.optional(),documentEpoch:handle.optional(),expression:str.optional(),duration:z.number().min(.1).max(10).default(2),limit:z.number().int().min(1).max(150).default(80),enable:z.array(z.enum(['Console','Network'])).max(2).optional()}).superRefine((a,c)=>{
    if(a.operation!=='pages'&&!a.pageId&&!a.contextId)c.addIssue({code:'custom',message:'List pages and provide pageId or a bound contextId',path:['pageId']});
    if(a.operation==='evaluate'&&!a.expression)c.addIssue({code:'custom',message:'evaluate requires expression',path:['expression']});
    if(a.operation!=='evaluate'&&a.expression!==undefined)c.addIssue({code:'custom',message:'expression applies only to evaluate',path:['expression']});
    if(a.operation!=='events'&&a.enable!==undefined)c.addIssue({code:'custom',message:'enable applies only to events',path:['enable']});
  }));
  tool('run_workflow','Run the packaged CLI build/install/setup workflows with explicit argv, e.g. ["xcode","sdks"], ["device","--udid",UDID,"install",IPA]. Fixed host/config; no shell or interactive prompts. See openrevive://cli. Returns actual exit status and bounded output. Invalidates snapshots and temporarily closes the persistent USB relay.',z.strictObject({host,arguments:z.array(str).min(1).max(128),timeout:z.number().int().min(1).max(3600).default(600)}));
  for (const [name, uri, relative] of [
    ['guide','openrevive://guide','../skills/legacy-mac/references/mcp.md'],
    ['cli','openrevive://cli','../skills/legacy-mac/SKILL.md'],
  ]) server.registerResource(name,uri,{mimeType:'text/markdown',description:`OpenRevive ${name}`},async () => ({contents:[{uri,mimeType:'text/markdown',text:readFileSync(new URL(relative,base),'utf8')}]}));
  return server;
}

const handleServer = serveStdio(factory,{onerror:error=>console.error('OpenRevive MCP:',error.message)});
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await backend.stop();
  await handleServer.close();
}
process.stdin.once('end',()=>void close());
for (const signal of ['SIGINT','SIGTERM','SIGHUP']) process.once(signal,()=>void close());
