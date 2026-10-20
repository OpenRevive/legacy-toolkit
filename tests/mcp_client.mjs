import { Client } from '../tools/mcp/node_modules/@modelcontextprotocol/client/dist/index.mjs';
import { StdioClientTransport } from '../tools/mcp/node_modules/@modelcontextprotocol/client/dist/stdio.mjs';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {homedir} from 'node:os';
import {createHash} from 'node:crypto';
export const ROOT = fileURLToPath(new URL('../',import.meta.url));
export const OUT = ROOT+'.artifacts/mcp/';
export const UDID = process.argv.includes('--udid') ? process.argv[process.argv.indexOf('--udid')+1] : undefined;
export async function connect(root=ROOT+'plugins/openrevive', cwd='/tmp', extra={}) {
  const transport = new StdioClientTransport({command:process.execPath,args:[root+'/mcp/server.mjs'],cwd,env:{...process.env},stderr:'pipe',maxBufferSize:16*1024*1024,...extra});
  let errors = '';
  transport.stderr.on('data',data=>{errors+=data.toString();});
  const client = new Client({name:'openrevive-acceptance',version:'0.6.0'});
  await client.connect(transport);
  return {client,transport,timings:[],errors:()=>errors,async close(){await client.close();}};
}
export async function call(connection,name,args={},expectError=false,options={}) {
  const start=performance.now();
  const raw = await connection.client.callTool({name,arguments:args},{timeout:120000,...options});
  connection.timings.push({tool:name,seconds:Math.round((performance.now()-start)*1000)/1000000});
  // Legacy and 2026 clients expose structured tool output with the same SDK API.
  const content = raw.structuredContent || JSON.parse(raw.content.find(c=>c.type==='text').text);
  if(raw.isError && /Another OpenRevive root command/.test(content.error || '')) {
    try {
      const key=createHash('sha256').update(args.udid || UDID).digest('hex');
      const holders=execFileSync('/usr/sbin/lsof',['-Fpc',homedir()+'/.config/openrevive/locks/'+key+'.lock'],{encoding:'utf8'});
      console.error('Root lease holders:',holders,'server PID:',connection.transport.pid);
      for(const match of holders.matchAll(/^p(\d+)$/gm)) console.error(execFileSync('/bin/ps',['-p',match[1],'-o','pid=,ppid=,pgid=,comm='],{encoding:'utf8'}));
    } catch {}
  }
  if (!expectError) assert.equal(raw.isError,undefined,JSON.stringify(content));
  else assert.equal(raw.isError,true,JSON.stringify(content));
  return {raw,...content};
}
export async function record(name,value) {
  await mkdir(OUT,{recursive:true});
  await writeFile(OUT+name+'.json',JSON.stringify(value,null,2)+'\n');
}
export async function image(connection,args,name) {
  const {raw,result} = await call(connection,'get_screenshot',args);
  const block = raw.content.find(c=>c.type==='image');
  assert.equal(block.mimeType,'image/png');
  const bytes = Buffer.from(block.data,'base64');
  assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
  assert.equal(bytes.readUInt32BE(16),result.width);
  assert.equal(bytes.readUInt32BE(20),result.height);
  await mkdir(OUT,{recursive:true});
  await writeFile(OUT+name+'.png',bytes);
  await record(name,result);
  return result;
}
export async function select(connection,tree,selectors) {
  const {result}=await call(connection,'find_ui',{snapshotId:tree.snapshotId,...selectors});
  assert.equal(result.count,1,JSON.stringify(result));
  return result.selected;
}
