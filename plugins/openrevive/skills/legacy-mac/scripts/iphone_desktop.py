"""Unified, explicitly partial UI discovery and owner-aware input routing."""
from __future__ import annotations
import json
from pathlib import Path
import time

from openrevive_lib import Failure
from iphone import private_root
from iphone_uikit import rpc,action_request
from usbmux_bridge import atomic_bytes


def automatic(node,kind):
    actions=node.get('actions',[])
    if kind=='ui-service':return None
    if kind=='springboard' and 'resolve-tap' in actions:return {'action':'tap','route':'hid'}
    if kind=='assistive-touch':return {'action':'tap','route':'vnc'} if 'resolve-tap' in actions else None
    for action in ('press','focus','activate'):
        if action in actions:return {'action':action,'route':'semantic'}
    if 'resolve-tap' in actions:return {'action':'tap','route':'hid'}
    return None


def merge(raw,udid,controller_captured=None):
    """Keep genuine local IDs and native owner snapshots under global process roots."""
    if raw.get('scope')!='desktop' or not isinstance(raw.get('owners'),list) or not isinstance(raw.get('processes'),list):
        raise Failure('Invalid desktop broker response')
    value={**raw,'controllerCapturedAt':time.time() if controller_captured is None else controller_captured,'udid':udid,'treeKind':'multiprocess-uikit-and-accessibility','schemaVersion':1}
    owners={o['pid']:o for o in raw['owners']}
    nodes=[{'id':'desktop','parent':None,'role':'desktop','label':'iPhone interface','actions':[],'actionable':False}]
    windows=[];trimmed=[];errors=[];truncated=[]
    for process in raw['processes']:
        pid=process['pid'];owner=owners.get(pid);prefix='p'+str(pid)
        nodes.append({'id':prefix,'parent':'desktop','role':'process','label':process.get('bundleId') or process['name'],
            'ownerPid':pid,'availability':process['status'],'uiCandidate':process['uiCandidate'],'actions':[],'actionable':False})
        if not owner:continue
        tree=owner.get('tree',{});kind=owner['ownerKind'];eligible=owner.get('screenEligible',False)
        metadata={k:v for k,v in owner.items() if k!='tree'}
        metadata['nodeCount']=len(tree.get('nodes',[]));metadata['snapshot']=tree.get('snapshot');trimmed.append(metadata)
        if owner.get('treeError'):errors.append({'pid':pid,**owner['treeError']})
        if tree.get('truncated') or owner.get('layoutTruncated'):truncated.append(pid)
        for window in owner.get('windows',[]):
            windows.append({**window,'id':prefix+'/'+window['id'],'parent':prefix,'ownerPid':pid,'ownerBundleId':owner['bundleId'],'ownerKind':kind,'screenEligible':eligible})
        for source in tree.get('nodes',[]):
            default=automatic(source,kind)
            actionable=bool(source.get('visible') and source.get('enabled') and source.get('locallyHittable') and eligible and default)
            node={**source,'id':prefix+'/'+source['id'],'parent':prefix+'/'+source['parent'] if source.get('parent') else prefix,
                'sourceId':source['id'],'windowId':prefix+'/'+source['windowId'],'ownerPid':pid,'ownerBundleId':owner['bundleId'],
                'ownerKind':kind,'ownerSession':owner['session'],'ownerSnapshot':tree['snapshot'],'screenEligible':eligible,
                'defaultAction':default,'actionable':actionable,'occlusion':source.get('occlusion','checked-at-dispatch')}
            if source.get('screenHittable') is False:node['actionable']=False
            if 'blockedByPid' in source:node['blockedByPid']=source['blockedByPid']
            nodes.append(node)
    required={raw['systemContext']['pid']}
    if raw['systemContext']['frontmostPid']:required.add(raw['systemContext']['frontmostPid'])
    required.update(p['pid'] for p in raw['processes'] if p['name']=='assistivetouchd')
    captured={o['pid'] for o in raw['owners'] if 'tree' in o}
    value.update({'owners':trimmed,'nodes':nodes,'windows':windows,'nodeCount':len(nodes),
        'uiNodeCount':sum(o['nodeCount'] for o in trimmed),
        'coverage':{'enumeratedProcesses':len(raw['processes']),'bridgedProcesses':len(owners),'capturedProcesses':len(captured),
            'completeForKnownScreenOwners':required<=captured and not any(e['pid'] in required for e in errors) and not raw.get('criticalUnavailableEndpoints') and not raw.get('discoveryTruncated'),
            'completeForBridgedOwners':len(captured)==len(owners) and not errors and not truncated and not raw.get('unavailableEndpoints') and not raw.get('discoveryTruncated'),
            'unbridgedUICandidates':raw.get('unbridgedUICandidates',[]),'unavailableTrees':errors,'unavailableEndpoints':raw.get('unavailableEndpoints',[]),'criticalUnavailableEndpoints':raw.get('criticalUnavailableEndpoints',[]),'truncatedOwners':truncated,
            'occlusionModel':'iOS6-window-level-plus-live-hit-tests; unknown unbridged UI has no inferred tree',
            'allKernelProcessesHaveUITrees':False}})
    return value


def snapshot(tunnel,udid,depth=16,limit=1000,hidden=False,output=None):
    if not 1<=depth<=20 or not 1<=limit<=1500:raise Failure('Tree depth must be 1..20 and limit 1..1500 per process')
    started=time.time()
    raw=rpc(tunnel,udid,'desktop',{'op':'tree','depth':depth,'limit':limit,'includeHidden':hidden})
    value=merge(raw,udid,started)
    if output:
        path=Path(output).expanduser().resolve()
        if not path.parent.is_dir():raise Failure('Snapshot output directory does not exist')
        value['snapshotFile']=str(path);atomic_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    return value


def check(snapshot,udid):
    if not isinstance(snapshot,dict) or snapshot.get('udid')!=udid or snapshot.get('scope')!='desktop' or (type(snapshot.get('schemaVersion')) is not int or snapshot['schemaVersion']!=1):
        raise Failure('Expected a unified snapshot from this device')
    if not isinstance(snapshot.get('snapshot'),str) or not snapshot['snapshot']:raise Failure('Missing desktop snapshot nonce')
    captured=snapshot.get('controllerCapturedAt')
    if type(captured) not in (float,int) or not 0<=time.time()-captured<=30:raise Failure('Desktop snapshot expired; read tree --all again')
    if not isinstance(snapshot.get('nodes'),list):raise Failure('Missing desktop nodes')


def find(snapshot,udid,*,label=None,contains=None,role=None,owner=None,identifier=None,cls=None,all_matches=False,include_unavailable=False):
    check(snapshot,udid)
    if not any(x is not None for x in (label,contains,role,owner,identifier,cls)):raise Failure('ui-find needs at least one selector')
    matches=[]
    for node in snapshot['nodes']:
        if not isinstance(node,dict) or not node.get('sourceId'):continue
        if not include_unavailable and not node.get('actionable'):continue
        if label is not None and node.get('label')!=label:continue
        if contains is not None and contains.casefold() not in str(node.get('label','')).casefold():continue
        if role is not None and node.get('role')!=role:continue
        if owner is not None and owner not in (str(node.get('ownerPid')),node.get('ownerBundleId')):continue
        if identifier is not None and node.get('identifier')!=identifier:continue
        if cls is not None and node.get('class')!=cls:continue
        matches.append(node)
    if not all_matches and len(matches)!=1:
        raise Failure('Expected one actionable match, found %d: %s; refine --owner/--role/--class/--identifier or refresh tree' % (len(matches),', '.join(n['id'] for n in matches[:12])))
    return {'snapshot':snapshot['snapshot'],'matches':matches,'count':len(matches),**({'selected':matches[0]} if len(matches)==1 else {})}


def action(tunnel,udid,snapshot,node_id,action,value=None):
    check(snapshot,udid)
    requested=action
    matches=[n for n in snapshot['nodes'] if isinstance(n,dict) and n.get('id')==node_id and n.get('sourceId')]
    if len(matches)!=1:raise Failure('Node is not unique in the unified snapshot')
    node=matches[0]
    if not node.get('actionable'):raise Failure('Node is hidden, covered, inactive or lacks a verified input route')
    owners=[o for o in snapshot.get('owners',[]) if o.get('pid')==node.get('ownerPid')]
    if len(owners)!=1:raise Failure('Missing or duplicate node owner')
    owner=owners[0]
    if node.get('ownerSession')!=owner.get('session') or node.get('ownerSnapshot')!=owner.get('snapshot') or node.get('ownerBundleId')!=owner.get('bundleId'):
        raise Failure('Unified node owner/session/snapshot mismatch')
    if action=='auto':
        if value is not None:raise Failure('auto takes no value; name a typed edit action')
        default=automatic(node,owner['ownerKind'])
        if not default:raise Failure('No verified automatic action for this node')
        action=default['action']
    # Reuse app action validation (typed values, advertised actions, schema).
    local={**owner,'udid':udid,'scope':'app','nodes':[{**node,'id':node['sourceId']}]}
    validated=action_request(local,udid,node['sourceId'],action,value)
    route='vnc' if owner['ownerKind']=='assistive-touch' and action=='tap' else 'hid' if action=='tap' else 'semantic'
    if owner['ownerKind']=='assistive-touch' and route!='vnc':raise Failure('AssistiveTouch currently has a verified VNC tap route only')
    request={'op':'action','snapshot':snapshot['snapshot'],'pid':owner['pid'],'node':node['sourceId'],
        'class':node['class'],'label':node['label'],'action':action,'route':route}
    if 'value' in validated:request['value']=validated['value']
    if route=='vnc':
        from iphone_vnc import VNC
        credential=private_root()/'devices'/udid/'vnc-password'
        if not credential.is_file():raise Failure('AssistiveTouch requires device setup-vnc and its private password')
        # Establish transport first, then resolve/revalidate immediately before input.
        channel = getattr(tunnel, 'vnc_channel', None)
        with (channel(udid) if channel else VNC(udid,credential.read_text().strip(),tunnel.env['USBMUXD_SOCKET_ADDRESS'])) as vnc:
            result=rpc(tunnel,udid,'desktop',request)
            if result.get('route')!='vnc' or not result.get('desktopConsumed'):raise Failure('Invalid VNC resolution receipt')
            frame=result.get('framePixels')
            if not isinstance(frame,list) or len(frame)!=4:raise Failure('Missing resolved target frame')
            x,y=round(frame[0]+frame[2]/2),round(frame[1]+frame[3]/2)
            if (vnc.width,vnc.height) not in ((640,960),(768,1024)) or not 0<=x<vnc.width or not 0<=y<vnc.height:raise Failure('Target outside the supported portrait framebuffer')
            vnc.click(x,y);result.update({'route':'vnc','dispatched':True,'tapPixels':[x,y]})
    else:result=rpc(tunnel,udid,'desktop',request)
    return {**result,'targetNode':node_id,'requestedAction':requested,'selectedAction':action,'requiresVerification':True}
