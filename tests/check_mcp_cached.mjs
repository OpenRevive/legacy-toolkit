import assert from 'node:assert/strict';
import {connect,call,image,record,UDID} from './mcp_client.mjs';
const [root,name]=process.argv.slice(2);
assert.ok(root && name && UDID);
const c=await connect(root,'/tmp');
try {
  const tools=await c.client.listTools();
  assert.equal(tools.tools.length,32);
  let mac,macScreen;
  if(!process.argv.includes('--iphone-only')){
    mac=(await call(c,'get_ui_tree',{target:'mac',pageSize:1})).result;
    assert.ok(mac.pid && mac.nodeCount>3);
    macScreen=await image(c,{target:'mac'},'cached-'+name+'-mac');
  }
  const phone=(await call(c,'get_ui_tree',{target:'iphone',udid:UDID,pageSize:1})).result;
  assert.ok(phone.owners.some(o=>o.ownerKind==='springboard'));
  assert.ok(phone.owners.some(o=>o.ownerKind==='assistive-touch'));
  const phoneScreen=await image(c,{target:'iphone',udid:UDID},'cached-'+name+'-iphone');
  await record('cached-'+name,{root,server:c.client.getServerVersion(),mac:mac?{pid:mac.pid,nodeCount:mac.nodeCount}:null,macScreen,phone:{owners:phone.owners.map(o=>({pid:o.pid,bundleId:o.bundleId,kind:o.ownerKind})),coverage:phone.coverage},phoneScreen});
  assert.equal(c.errors(),'');
} finally {await c.close();}
console.log('OPENREVIVE_MCP_CACHED_OK');
