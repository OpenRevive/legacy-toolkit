"""UIKit snapshots, guarded element actions and iOS 6 system multitouch."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import math
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import tempfile
import time

from openrevive_lib import Failure
from device_services import Device, AFC
from iphone import private_root, root_exec
from usbmux_bridge import atomic_bytes

VERSION='0.5.0'
DEVICE_ROOT='/var/root/OpenRevive'
LIBRARIES='/Library/MobileSubstrate/DynamicLibraries'
CLIENT=DEVICE_ROOT+'/legacy-uikit-client'
SOURCES=('legacy-uikit.m','legacy-hid.m','legacy-uikit-client.m','legacy-system.h','legacy-context.h','legacy-alert-probe.h','legacy-desktop.h')


@contextlib.contextmanager
def lease(udid):
    from device_sessions import check_control
    check_control(udid)
    directory=private_root()/'locks';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (directory/(hashlib.sha256(udid.encode()).hexdigest()+'.uikit.lock')).open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise Failure('Another UIKit/gesture operation owns this phone') from error
        yield


def rpc(tunnel,udid,endpoint,request,check_result=True):
    data=json.dumps(request,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(data.encode())>190000:raise Failure('UIKit/HID request exceeds 190 KB')
    result=root_exec(tunnel,udid,[CLIENT,endpoint,data],timeout=18)
    if result.code:raise Failure('Device bridge: '+result.stderr.decode(errors='replace').strip(),result.code)
    try:value=json.loads(result.stdout)
    except ValueError as error:raise Failure('Device bridge returned invalid JSON') from error
    if not isinstance(value,dict):raise Failure('Device bridge returned a non-object')
    if check_result and not value.get('ok'):
        raise Failure('Device bridge %s: %s' % (value.get('error','failed'),value.get('message','')))
    return value


def bundle_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{1,199}',value):
        raise Failure('Expected an explicit bundle identifier')
    return value


def tree(tunnel,udid,bundle=None,depth=8,limit=500,hidden=False,output=None,system=False):
    bundle="com.apple.springboard" if system else bundle_id(bundle)
    if not 1<=depth<=20 or not 1<=limit<=1500:raise Failure('Tree depth must be 1..20 and limit 1..1500')
    value=rpc(tunnel,udid,'system' if system else 'bundle:'+bundle,{'op':'tree','depth':depth,'limit':limit,'includeHidden':hidden,'scope':'system' if system else 'app'})
    if value.get('bundleId')!=bundle or not isinstance(value.get('nodes'),list) or not value.get('snapshot'):
        raise Failure('UIKit snapshot owner/schema mismatch')
    value['udid']=udid
    if output:
        path=Path(output).expanduser().resolve()
        if not path.parent.is_dir():raise Failure('Snapshot output directory does not exist')
        value['snapshotFile']=str(path)
        atomic_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    return value


def system_guard(snapshot,udid):
    if not isinstance(snapshot,dict) or snapshot.get('udid')!=udid or snapshot.get('scope')!='system' or snapshot.get('bundleId')!='com.apple.springboard':
        raise Failure('Expected a system snapshot from this device')
    context=snapshot.get('systemContext')
    if not isinstance(context,dict):raise Failure('Snapshot lacks its system context')
    for key in ('token','session','frontmostBundleId','alertIdentity','surface'):
        if not isinstance(context.get(key),str):raise Failure('Invalid system context '+key)
    for key in ('screenLocked','passcodeEnabled'):
        if type(context.get(key)) is not bool:raise Failure('Invalid system context '+key)
    if type(context.get('frontmostPid')) is not int or context['frontmostPid']<0:raise Failure('Invalid foreground PID')
    if type(snapshot.get('pid')) is not int or snapshot['pid']<1 or snapshot['pid']!=context.get('pid') or snapshot.get('session')!=context['session']:
        raise Failure('System snapshot process/session mismatch')
    if not isinstance(snapshot.get('snapshot'),str) or not snapshot['snapshot']:raise Failure('System snapshot nonce is missing')
    return {**{key:snapshot[key] for key in ('bundleId','pid','session','snapshot')},'scope':'system',
        'systemToken':context['token'],'systemContext':context}


def action_request(snapshot,udid,node,action,value=None):
    if not isinstance(snapshot,dict) or snapshot.get('udid')!=udid:raise Failure('Snapshot belongs to another device')
    bundle_id(snapshot.get('bundleId'))
    if type(snapshot.get('pid')) is not int or snapshot['pid']<1 or any(not isinstance(snapshot.get(key),str) or not snapshot[key] for key in ('snapshot','session')):
        raise Failure('Snapshot lacks its process/session/owner guards')
    nodes=snapshot.get('nodes')
    if not isinstance(nodes,list) or any(not isinstance(n,dict) for n in nodes):raise Failure('Invalid snapshot nodes')
    matches=[n for n in nodes if n.get('id')==node]
    if len(matches)!=1:raise Failure('Node ID is not unique in this snapshot')
    target=matches[0]
    if any(not isinstance(target.get(key),str) for key in ('class','label')) or not isinstance(target.get('actions'),list) or any(not isinstance(x,str) for x in target['actions']):
        raise Failure('Invalid node identity/actions schema')
    native_action='resolve-tap' if action=='tap' else action
    if native_action not in target.get('actions',[]):raise Failure('Action is not advertised by this node')
    if not target.get('visible') or not target.get('enabled'):raise Failure('Snapshot node is hidden or disabled')
    if action=='set-text':
        if not isinstance(value,str) or len(value.encode('utf-16-le'))>8192:raise Failure('Text must have at most 4096 UTF-16 units')
    elif action=='set-on':
        if type(value) is not bool:raise Failure('set-on needs true or false')
    elif action=='set-value':number(value,'Slider value')
    elif action=='scroll':
        if not isinstance(value,list) or len(value)!=2:raise Failure('scroll needs JSON [x,y] in UIKit points')
        for x in value:number(x,'Scroll offset')
    elif value is not None:raise Failure('This action takes no value')
    request={key:snapshot[key] for key in ('bundleId','pid','session','snapshot')}
    request.update({'op':'action','node':node,'class':target['class'],'label':target['label'],'action':native_action})
    if value is not None:request['value']=value
    if snapshot.get('scope')=='system':request.update(system_guard(snapshot,udid))
    elif snapshot.get('scope','app')!='app':raise Failure('Unknown snapshot scope')
    return request


def act(tunnel,udid,snapshot,node,action,value=None):
    request=action_request(snapshot,udid,node,action,value)
    system=request.get('scope')=='system'
    result=rpc(tunnel,udid,'system' if system else 'bundle:'+request['bundleId'],request)
    if action=='tap':
        display=rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'})
        if result.get('screenPixels')!=[0,0,*display.get('framebuffer',[])]:
            raise Failure('Tap coordinate space differs from the physical framebuffer (iPhone compatibility mode); use a fresh screenshot and explicit physical gesture')
        frame=result.get('framePixels',[])
        if len(frame)!=4:raise Failure('Tap resolution returned no rectangle')
        x,y=frame[0]+frame[2]/2,frame[1]+frame[3]/2
        gesture={'fingers':[{'id':1,'points':[{'t':0,'x':x,'y':y},{'t':.08,'x':x,'y':y}]}]}
        result=play(tunnel,udid,request['bundleId'],gesture,pid=request['pid'],system_context=result.get('systemContext') if system else None)
    return result


def number(value,label):
    if type(value) not in (int,float) or not math.isfinite(value):raise Failure(label+' must be a finite number')
    return float(value)


def compile_gesture(value,width=640,height=960):
    """Tracks use framebuffer pixels and independent down/up times, max 5 s."""
    if not isinstance(value,dict):raise Failure('Gesture must be a JSON object')
    if (width,height) not in ((640,960),(768,1024)):raise Failure('This driver requires a supported portrait framebuffer: 640x960 or 768x1024')
    fingers=value.get('fingers');fps=value.get('fps',30)
    if not isinstance(fingers,list) or not 1<=len(fingers)<=5:raise Failure('Gesture needs 1..5 finger tracks')
    if type(fps) is not int or not 10<=fps<=60:raise Failure('fps must be an integer in 10..60')
    ids=set();tracks=[];times=set()
    for finger in fingers:
        if not isinstance(finger,dict):raise Failure('Finger track must be an object')
        ident=finger.get('id');points=finger.get('points')
        if type(ident) is not int or not 1<=ident<=5 or ident in ids:raise Failure('Finger IDs must be unique integers in 1..5')
        ids.add(ident)
        if not isinstance(points,list) or not 2<=len(points)<=64:raise Failure('Each finger needs 2..64 timed points')
        parsed=[];previous=-1
        for point in points:
            if not isinstance(point,dict):raise Failure('Point must be an object')
            t=number(point.get('t'),'Point time');x=number(point.get('x'),'Point x');y=number(point.get('y'),'Point y')
            if not 0<=t<=5 or t<=previous or not 0<=x<width or not 0<=y<height:
                raise Failure('Point time must increase in 0..5 s and position must be inside the framebuffer')
            t=round(t,6)
            if t<=previous:raise Failure('Point times must differ by at least one microsecond')
            parsed.append((t,x,y));times.add(t);previous=t
        if parsed[-1][0]-parsed[0][0]<.02:raise Failure('Each contact must last at least 20 ms')
        tracks.append((ident,parsed))
    duration=max(points[-1][0] for ident,points in tracks)
    if duration<.02:raise Failure('Gesture duration is too short')
    for i in range(math.ceil(duration*fps)+1):times.add(round(min(i/fps,duration),6))
    frames=[]
    for t in sorted(times):
        contacts=[]
        for ident,points in tracks:
            if not points[0][0]<=t<=points[-1][0]:continue
            phase='down' if t==points[0][0] else 'up' if t==points[-1][0] else 'move'
            left,right=points[0],points[-1]
            for i in range(1,len(points)):
                if t<=points[i][0]:left,right=points[i-1],points[i];break
            f=(t-left[0])/(right[0]-left[0])
            contacts.append({'id':ident,'phase':phase,'x':left[1]+f*(right[1]-left[1]),'y':left[2]+f*(right[2]-left[2])})
        if contacts:frames.append({'t':t,'contacts':contacts})
    if not 2<=len(frames)<=305:raise Failure('Gesture exceeds 305 frames; reduce fps or point count')
    return {'width':width,'height':height,'frames':frames}


def pinch_definition(cx,cy,start,end,duration=1,angle=0,width=640,height=960):
    for key,value in [('center x',cx),('center y',cy),('start span',start),('end span',end),('duration',duration),('angle',angle)]:number(value,key)
    if start<=0 or end<=0 or not .02<=duration<=5:raise Failure('Pinch spans must be positive and duration .02..5 seconds')
    dx,dy=math.cos(math.radians(angle))/2,math.sin(math.radians(angle))/2
    fingers=[]
    for ident,sign in [(1,-1),(2,1)]:
        fingers.append({'id':ident,'points':[{'t':0,'x':cx+sign*start*dx,'y':cy+sign*start*dy},
            {'t':duration,'x':cx+sign*end*dx,'y':cy+sign*end*dy}]})
    result={'fingers':fingers};compile_gesture(result,width,height);return result


def play(tunnel,udid,bundle,definition,pid=None,system_snapshot=None,system_context=None):
    display=rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'})
    dimensions=display.get('framebuffer',[])
    if not isinstance(dimensions,list) or len(dimensions)!=2:raise Failure('HID bridge did not report its framebuffer')
    packet=compile_gesture(definition,*dimensions)
    if system_snapshot is not None:
        guard=system_guard(system_snapshot,udid)
        guard['op']='validate'
        validated=rpc(tunnel,udid,'system',guard)
        system_context=validated['systemContext']
    if system_context is not None:
        bundle='com.apple.springboard';pid=system_context['pid']
        packet.update({'scope':'system','systemContext':system_context})
    bundle_id(bundle)
    packet.update({'op':'play','bundleId':bundle})
    if pid is not None:packet['pid']=pid
    return rpc(tunnel,udid,'org.openrevive.hid',packet)


def setup(tunnel,udid,respring=False):
    version=tunnel.tool('ideviceinfo',['-u',udid,'-k','ProductVersion']).stdout.decode().strip()
    product=tunnel.tool('ideviceinfo',['-u',udid,'-k','ProductType']).stdout.decode().strip()
    if version.startswith('6.') and product=='iPhone3,1':width,height,process,minimum=640,960,'backboardd','6.0'
    elif version=='5.1.1' and product=='iPad1,1':width,height,process,minimum=768,1024,'SpringBoard','5.0'
    else:raise Failure('UIKit/multitouch supports iPhone3,1 on iOS 6 and iPad1,1 on iOS 5.1.1; this device needs a separate port')
    state=root_exec(tunnel,udid,[DEVICE_ROOT+'/legacy-device','state'],timeout=10)
    if state.code or json.loads(state.stdout).get('uid')!=0:raise Failure('Run bootstrap-control first; root control is unavailable')
    directory=private_root()/'devices'/udid;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    credential=directory/'uikit-token'
    if not credential.exists():atomic_bytes(credential,secrets.token_hex(32).encode())
    token=credential.read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}',token):raise Failure('Invalid private UIKit token')
    source_dir=Path(__file__).parent/'remote'
    source={name:(source_dir/name).read_bytes() for name in SOURCES}
    profile=json.dumps({'width':width,'height':height,'process':process,'minimum':minimum},sort_keys=True).encode()
    digest=hashlib.sha256(b''.join(source[name] for name in SOURCES)+token.encode()+profile).hexdigest()
    previous=None
    with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
        try:previous=json.loads(afc.read(DEVICE_ROOT+'/uikit-install.json'))
        except (RuntimeError,ValueError):pass
        expected_paths={CLIENT,LIBRARIES+'/OpenReviveUIKit.dylib',LIBRARIES+'/OpenReviveHID.dylib',
            LIBRARIES+'/OpenReviveUIKit.plist',LIBRARIES+'/OpenReviveHID.plist',DEVICE_ROOT+'/uikit-token'}
        intact=previous and previous.get('sourceDigest')==digest and set(previous.get('files',{}))==expected_paths
        if intact:
            for path,expected in previous.get('files',{}).items():
                try:actual=hashlib.sha256(afc.read(path)).hexdigest()
                except RuntimeError:intact=False;break
                if actual!=expected:intact=False;break
        if intact and not respring:
            try:
                status=rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'},check_result=False)
                system=rpc(tunnel,udid,'system',{'op':'context'},check_result=False)
            except Failure:status={};system={}
            if status.get('buildId')==digest and status.get('callbackReady') and system.get('buildId')==digest:
                return {'udid':udid,'version':VERSION,'alreadyInstalled':True,'ready':True,'sourceDigest':digest}
    mac=tunnel.mac;remote=mac.path('work','build','uikit-'+digest[:16])
    mac.python('import os,sys;os.makedirs(sys.argv[1]) if not os.path.isdir(sys.argv[1]) else None',[remote])
    import base64
    for name,data in source.items():
        mac.python('import sys,base64;open(sys.argv[1],"wb").write(base64.b64decode(sys.argv[2]))',[remote+'/'+name,base64.b64encode(data).decode()])
    sdk=mac.execute(['/usr/bin/xcodebuild','-version','-sdk','iphoneos6.1','Path']).check().stdout.decode().strip()
    developer=mac.execute(['/usr/bin/xcode-select','-print-path']).check().stdout.decode().strip()
    paths={'legacy-uikit.m':LIBRARIES+'/OpenReviveUIKit.dylib','legacy-hid.m':LIBRARIES+'/OpenReviveHID.dylib','legacy-uikit-client.m':CLIENT}
    staged={}
    with tempfile.TemporaryDirectory(prefix='openrevive-uikit-') as temporary:
        for name,target in paths.items():
            binary=remote+'/'+Path(target).name
            argv=[developer+'/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang','-Wall','-Wextra','-Werror',
                '-Wno-unused-parameter','-arch','armv7','-isysroot',sdk,'-miphoneos-version-min='+minimum,
                '-framework','Foundation','-framework','CoreFoundation','-framework','CoreGraphics']
            if name!='legacy-uikit-client.m':
                argv+=['-dynamiclib','-DOR_AUTH_TOKEN=@"'+token+'"','-DOR_BUILD_ID=@"'+digest+'"']
            if name=='legacy-hid.m':argv+=['-DOR_WIDTH='+str(width),'-DOR_HEIGHT='+str(height)]
            if name=='legacy-uikit.m':argv+=['-framework','UIKit']
            mac.execute([*argv,remote+'/'+name,'-o',binary],timeout=60).check()
            local=Path(temporary)/Path(target).name;mac.pull(binary,local);staged[target]=local.read_bytes()
    staged[LIBRARIES+'/OpenReviveUIKit.plist']=plistlib.dumps({'Filter':{'Bundles':['com.apple.UIKit']}})
    staged[LIBRARIES+'/OpenReviveHID.plist']=plistlib.dumps({'Filter':{'Executables':[process]}})
    staged[DEVICE_ROOT+'/uikit-token']=token.encode()
    suffix='.openrevive-new-'+digest[:12]
    with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
        for target,data in staged.items():
            afc.write(target+suffix,data)
            if afc.read(target+suffix)!=data:raise Failure('UIKit staging readback mismatch')
    commands=[]
    for target in paths.values():
        commands+=['/bin/chmod 755 '+shlex.quote(target+suffix),DEVICE_ROOT+'/ldid -S '+shlex.quote(target+suffix)]
    commands+=['/bin/chmod 600 '+shlex.quote(DEVICE_ROOT+'/uikit-token'+suffix)]
    for target in staged:commands+=['/bin/mv -f '+shlex.quote(target+suffix)+' '+shlex.quote(target)]
    result=root_exec(tunnel,udid,[DEVICE_ROOT+'/legacy-device','run','25','/bin/bash','-c',' && '.join(commands)],timeout=30)
    if result.code:raise Failure('UIKit publication failed: '+result.stderr.decode(errors='replace'),result.code)
    with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
        files={target:hashlib.sha256(afc.read(target)).hexdigest() for target in staged}
        afc.write(DEVICE_ROOT+'/uikit-install.json',json.dumps({'version':VERSION,'sourceDigest':digest,'files':files}).encode())
    if respring:
        result=root_exec(tunnel,udid,[DEVICE_ROOT+'/legacy-device','run','10','/usr/bin/killall',process],timeout=15)
        if result.code:raise Failure('UI restart failed: '+result.stderr.decode(errors='replace'),result.code)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                status=rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'})
                system=rpc(tunnel,udid,'system',{'op':'context'})
            except Failure:time.sleep(.3);continue
            if status.get('callbackReady') and status.get('buildId')==digest and system.get('buildId')==digest:
                return {'udid':udid,'version':VERSION,'ready':True,'respringRequested':True,'sourceDigest':digest,'next':'Wake/unlock the phone, launch the target app and read a tree.'}
            time.sleep(.3)
        raise Failure('HID and SpringBoard endpoints did not become ready after the UI restart; inspect device syslog')
    return {'udid':udid,'version':VERSION,'installed':True,'ready':False,'respringRequired':True,'sourceDigest':digest,
        'next':'Run setup-uikit --respring to load the new libraries. Existing apps must restart.'}


def command(tunnel,udid,args,emit):
    with lease(udid):
        name=args.device
        if name=='setup-uikit':result=setup(tunnel,udid,args.respring)
        elif name=='ui-status':
            result={'hid':rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'})}
            if args.bundle:result['app']=rpc(tunnel,udid,'bundle:'+bundle_id(args.bundle),{'op':'ping'})
            if args.system:result['system']=rpc(tunnel,udid,'system',{'op':'context'})
        elif name=='system-alert-probe':
            state=rpc(tunnel,udid,'system',{'op':'context'})['systemContext']
            if state['screenLocked'] or state['alertClass']:raise Failure('Unlock/dismiss the current alert before showing the disposable probe')
            result=rpc(tunnel,udid,'probe-alert',{'nonce':secrets.token_hex(16)})
        elif name=='system-state':result=rpc(tunnel,udid,'system',{'op':'context'})
        elif name=='system-control':
            guard=system_guard(json.loads(Path(args.snapshot_file).expanduser().read_text()),udid)
            guard.update({'op':'system-control','action':args.action});result=rpc(tunnel,udid,'system',guard)
        elif name=='ui-processes':result=rpc(tunnel,udid,'desktop',{'op':'discover'})
        elif name=='tree':
            if args.summary and not args.output:raise Failure('--summary requires --output so the full snapshot remains available')
            if args.all_processes:
                from iphone_desktop import snapshot
                result=snapshot(tunnel,udid,args.depth,args.limit,args.include_hidden,args.output)
            else:result=tree(tunnel,udid,args.bundle,args.depth,args.limit,args.include_hidden,args.output,system=args.system)
            if args.summary:
                result={**{k:v for k,v in result.items() if k in ('ok','scope','snapshot','snapshotFile','udid','bundleId','pid','session','nodeCount','uiNodeCount','coverage','truncated','capturedAt','controllerCapturedAt','expiresInSeconds')},
                    **({'owners':[{k:o[k] for k in ('pid','bundleId','ownerKind','nodeCount','screenEligible')} for o in result['owners']]} if 'owners' in result else {})}
        elif name=='ui-action':
            snapshot=json.loads(Path(args.snapshot_file).expanduser().read_text())
            value=args.value
            if args.action in ('set-on','set-value','scroll'):
                try:value=json.loads(value) if value is not None else None
                except ValueError as error:raise Failure('Expected a JSON boolean, number or [x,y]') from error
            if snapshot.get('scope')=='desktop':
                from iphone_desktop import action
                result=action(tunnel,udid,snapshot,args.node,args.action,value)
            else:
                if args.action=='auto':raise Failure('auto requires tree --all')
                result=act(tunnel,udid,snapshot,args.node,args.action,value)
        elif name in ('tap','swipe'):
            snapshot=json.loads(Path(args.system_snapshot).expanduser().read_text())
            x1,y1,x2,y2,duration=(args.x,args.y,args.x,args.y,.08) if name=='tap' else (args.x1,args.y1,args.x2,args.y2,args.duration)
            definition={'fingers':[{'id':1,'points':[{'t':0,'x':x1,'y':y1},{'t':duration,'x':x2,'y':y2}]}]}
            result=play(tunnel,udid,None,definition,system_snapshot=snapshot)
        elif name=='gesture':
            path=Path(args.gesture_file).expanduser()
            if path.stat().st_size>100000:raise Failure('Gesture file exceeds 100 KB')
            snapshot=json.loads(Path(args.system_snapshot).expanduser().read_text()) if args.system_snapshot else None
            result=play(tunnel,udid,args.bundle,json.loads(path.read_text()),system_snapshot=snapshot)
        elif name=='pinch':
            snapshot=json.loads(Path(args.system_snapshot).expanduser().read_text()) if args.system_snapshot else None
            display=rpc(tunnel,udid,'org.openrevive.hid',{'op':'ping'})['framebuffer']
            result=play(tunnel,udid,args.bundle,pinch_definition(args.x,args.y,args.start_span,args.end_span,args.duration,args.angle,*display),system_snapshot=snapshot)
        else:raise Failure('Unsupported UIKit command')
        emit(result);return 0
