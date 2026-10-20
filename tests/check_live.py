#!/usr/bin/env python3
"""Real BlackLion acceptance, confined to OpenRevive's probes and test directories."""
import hashlib
import json
import os
import plistlib
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'plugins/openrevive/skills/legacy-mac/scripts'
sys.path.insert(0,str(SCRIPTS))
import openrevive_lib as library
from openrevive_lib import Mac, configuration_path

OUT=ROOT/'.artifacts'
OUT.mkdir(exist_ok=True)
MAC=Mac.from_config(configuration_path(),'blacklion')
TIMINGS=[]


def log(name,result):
    (OUT/(name+'.stdout')).write_bytes(result.stdout)
    (OUT/(name+'.stderr')).write_bytes(result.stderr)
    result.check()
    return result


def ui(*args,check=True):
    start=time.monotonic()
    result=MAC.ui(*args,check=check)
    TIMINGS.append({'command':list(args[:1]),'seconds':round(time.monotonic()-start,3)})
    return result.json() if check else result


def cleanup(root):
    MAC.python('import sys,os,shutil; p=sys.argv[1]; assert "openrevive-test-" in os.path.basename(p); shutil.rmtree(p)',[root])


def setup():
    assert MAC.bootstrap(check=True).json()['osVersion'] == '10.7.5'
    profile=dict(MAC.profile,remote_root='~/Library/Application Support/openrevive-test-setup-'+uuid.uuid4().hex)
    isolated=Mac(profile)
    first=log('setup-first',isolated.bootstrap()).json()
    assert first['installed'] and not first['alreadyInstalled']
    second=log('setup-repeat',isolated.bootstrap()).json()
    assert second['alreadyInstalled'] and first['release']==second['release']
    # Repair a known damaged generated artifact in this disposable installation.
    isolated.python('import sys; f=open(sys.argv[1],"ab"); f.write("damage-fixture"); f.close()', [isolated.path('current','legacy-ui')])
    repaired=log('setup-repair',isolated.bootstrap()).json()
    assert not repaired['alreadyInstalled'] and repaired['release']==first['release']
    with tempfile.TemporaryDirectory() as directory:
        payload=Path(directory)/'remote'
        shutil.copytree(library.PAYLOAD,payload,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        with (payload/'legacy-ui.m').open('a') as stream: stream.write('\n// Disposable rollback test variant.\n')
        with patch.object(library,'PAYLOAD',payload):
            changed=log('setup-new-version',isolated.bootstrap()).json()
        assert changed['release'] != first['release']
        assert isolated.rollback().json()['release']==first['release']
    cleanup(isolated.root)
    installed=log('bootstrap-current',MAC.bootstrap()).json()
    doctor=MAC.doctor()
    (OUT/'doctor.json').write_bytes(doctor.stdout)
    doctor.check()
    assert doctor.json()['ready']
    compiled=MAC.python('import os,sys,json; r=sys.argv[1]; paths=[os.path.join(r,p) for p in os.listdir(r) if p.endswith(".py")]; [compile(open(p).read(),p,"exec") for p in paths]; print(json.dumps({"python2ScriptsCompiled":len(paths)}))',[MAC.path('current')]).json()
    assert compiled['python2ScriptsCompiled'] >= 5
    print(json.dumps({'fresh':first,'repeated':second,'repaired':repaired,'current':installed,'python2':compiled}))
    print('OPENREVIVE_SETUP_OK')


def controls(pid):
    tree=ui('tree',pid,7,180)
    nodes=tree['nodes']
    window=next(n for n in nodes if n['role']=='AXWindow' and n['title']=='OpenRevive GUI Probe')
    own=[n for n in nodes if n['path'].startswith(window['path']+'.')]
    field=next(n for n in own if n['role']=='AXTextField')
    button=next(n for n in own if n['role']=='AXButton' and n['title']=='Apply probe')
    return tree,window,field,button


def center(node):
    x,y,w,h=node['frame']
    return round(x+w/2),round(y+h/2)


def receipt():
    return MAC.python('import os,sys; sys.stdout.write(open(os.path.expanduser("~/Library/Logs/OpenReviveProbe/result.json")).read())').json()


def gui():
    assert ui('info')['accessibility']
    MAC.execute(['/usr/bin/python','-c','from AppKit import NSRunningApplication; [a.terminate() for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_("org.openrevive.Probe")]']).check()
    time.sleep(.3)
    MAC.execute(['/usr/bin/open',MAC.path('current','OpenReviveProbe.app')]).check()
    apps=[]
    for _ in range(25):
        apps=[a for a in ui('apps') if a['bundleId']=='org.openrevive.Probe']
        if len(apps)==1: break
        time.sleep(.1)
    assert len(apps)==1,apps
    pid=apps[0]['pid']; bundle=apps[0]['bundleId']
    ui('wake'); ui('focus',pid)
    assert ui('info')['frontmostPid']==pid
    tree,window,field,button=controls(pid)
    for role,title,owner,error in [('AXButton',field['title'],bundle,'role/title changed'),
        (field['role'],'incorrect title',bundle,'role/title changed'),
        (field['role'],field['title'],'org.openrevive.WrongOwner','Owning application changed')]:
        denied=ui('set',pid,field['path'],'guard rejected',role,title,owner,check=False)
        assert denied.code and error in denied.stderr.decode(),denied
        assert controls(pid)[2]['value']==field['value']
    text='OpenRevive AX: Привет 日本語'
    ui('set',pid,field['path'],text,field['role'],field['title'],bundle)
    tree,window,field,button=controls(pid)
    ui('action',pid,button['path'],'AXPress',button['role'],button['title'],bundle)
    first=receipt(); assert first['text']==text and text in first['visibleResult']
    tree,window,field,button=controls(pid)
    ui('click',*center(field)); ui('key',0,'cmd')
    text='OpenRevive: Привет 日本語'
    ui('type',text)
    assert controls(pid)[2]['value']==text
    ui('click',*center(button))
    second=receipt(); assert second['text']==text and second['clicks']==first['clicks']+1
    ui('move',600,150); assert ui('info')['cursor']==[600,150]
    tree,window,field,button=controls(pid)
    x,y,w,h=field['frame']
    ui('double-click',round(x+40),round(y+h/2))
    selected=controls(pid)[2].get('selectedText','')
    assert selected, 'Double click did not select a word'
    x,y,w,h=window['frame']
    ui('drag',round(x+w/2),round(y+10),round(x+w/2+35),round(y+30))
    moved=controls(pid)[1]['frame']
    assert abs(moved[0]-x-35)<3 and abs(moved[1]-y-20)<3,(window,moved)
    tree,window,field,button=controls(pid)
    scroller=next(n for n in tree['nodes'] if n['role']=='AXScrollBar' and n.get('frame',[0,0,0,0])[3]>30)
    area=next(n for n in tree['nodes'] if n['role']=='AXScrollArea')
    before=float(scroller['value'])
    ui('move',*center(area)); ui('scroll',-8,0)
    after=next(n for n in controls(pid)[0]['nodes'] if n['path']==scroller['path'])
    assert float(after['value'])>before,(before,after)
    screen=MAC.screenshot(OUT/'native.png')
    assert (screen['width'],screen['height'])==(ui('info')['width'],ui('info')['height'])
    (OUT/'native-tree.json').write_text(json.dumps(controls(pid)[0],ensure_ascii=False,indent=2))
    record={'receipt':second,'screen':screen,'selectedText':selected,'dragFrame':moved,
        'scrollBefore':before,'scrollAfter':after['value'],'timings':TIMINGS}
    (OUT/'gui.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(record,ensure_ascii=False))
    print('OPENREVIVE_GUI_OK')


def workflow():
    root=MAC.expand('~/Library/Application Support/openrevive-test-work-'+uuid.uuid4().hex)
    with tempfile.TemporaryDirectory() as directory:
        local=Path(directory)
        sample='quote\' \" dollar$ backtick` 日本語\n'.encode()
        (local/'odd name $` 日本語.txt').write_bytes(sample)
        MAC.sync(local,root)
        MAC.python('import sys; open(sys.argv[1],"w").write("keep")',[root+'/remote-only.txt'])
        MAC.sync(local,root)
        MAC.execute(['/bin/test','-f',root+'/remote-only.txt']).check()
        retrieved=local/'pulled.bin'; MAC.pull(root+'/odd name $` 日本語.txt',retrieved)
        assert retrieved.read_bytes()==sample
    arguments=['space value',"single'quote",'$(touch /tmp/openrevive-must-not-exist)', '`echo literal`', 'Привет 日本語', 'line1\nline2','--yes']
    output=MAC.execute(['/usr/bin/python','-c','import json,sys,os; print(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"env":os.environ["OPENREVIVE_TEST"]}))',*arguments],cwd=root,env={'OPENREVIVE_TEST':'literal $()'}).json()
    assert output=={'args':arguments,'cwd':root,'env':'literal $()'},output
    for aqua in (False,True):
        assert MAC.execute(['/bin/launchctl','managername'],aqua=aqua).stdout.strip()==(b'Aqua' if aqua else b'Background')
        failed=MAC.execute(['/usr/bin/python','-c','import sys; print("ordinary output"); sys.stderr.write("expected failure\\n"); sys.exit(7)'],aqua=aqua)
        assert failed.code==7 and b'expected failure' in failed.stderr and b'ordinary output' in failed.stdout
        pidfile=root+('/aqua.pid' if aqua else '/background.pid')
        timeout=MAC.execute(['/usr/bin/python','-c','import os,sys,time; open(sys.argv[1],"w").write(str(os.getpid())); time.sleep(47)',pidfile],timeout=1,aqua=aqua)
        assert timeout.code==124,timeout
        time.sleep(.4)
        gone=MAC.python('import os,sys,json; p=int(open(sys.argv[1]).read()); alive=True\ntry: os.kill(p,0)\nexcept OSError: alive=False\nprint(json.dumps({"alive":alive}))',[pidfile]).json()
        assert not gone['alive'],gone
    jobs=MAC.execute(['/bin/launchctl','list']).stdout
    assert b'org.openrevive.aqua-' not in jobs
    # The CLI parser, not only the library, must preserve its child's status.
    cli=subprocess.run([str(ROOT/'bin/openrevive'),'aqua','--timeout','15','--','/usr/bin/python','-c','import sys; sys.exit(9)'],capture_output=True)
    assert cli.returncode==9,cli
    for sdk in ('iphonesimulator6.1','iphoneos6.1'):
        result=MAC.execute(['/bin/bash',MAC.path('current','build-ios.sh'),sdk,MAC.path('work','build')],timeout=120)
        log('build-'+sdk,result)
        assert b'OpenReviveTouchProbe.app' in result.stdout
    for sdk,arch in [('iphonesimulator6.1','i386'),('iphoneos6.1','armv7')]:
        binary=MAC.path('work','build',sdk,'OpenReviveTouchProbe.app','OpenReviveTouchProbe')
        assert arch.encode() in MAC.execute(['/usr/bin/xcrun','lipo','-info',binary]).stdout
    MAC.python('import sys,os,shutil; src,dst=sys.argv[1:]; [shutil.copy2(os.path.join(src,n),os.path.join(dst,n)) for n in ("TouchProbe.m","TouchProbe-Info.plist")]; shutil.copytree(os.path.join(src,"TouchProbe.xcodeproj"),os.path.join(dst,"TouchProbe.xcodeproj"))',[MAC.path('current'),root])
    for sdk,arch in [('iphonesimulator6.1','i386'),('iphoneos6.1','armv7')]:
        command=[str(ROOT/'bin/openrevive'),'xcode','build',root+'/TouchProbe.xcodeproj',
            '--sdk',sdk,'--unsigned','--setting','ARCHS='+arch,
            '--setting','CONFIGURATION_BUILD_DIR='+root+'/xcode-'+sdk,
            '--setting','OBJROOT='+root+'/obj-'+sdk,'--setting','SYMROOT='+root+'/sym-'+sdk]
        built=subprocess.run(command,capture_output=True)
        (OUT/('xcode-'+sdk+'.stdout')).write_bytes(built.stdout)
        (OUT/('xcode-'+sdk+'.stderr')).write_bytes(built.stderr)
        assert built.returncode==0 and b'** BUILD SUCCEEDED **' in built.stdout,(built.returncode,built.stderr[-2000:],built.stdout[-2000:])
    sdks=MAC.execute([MAC.path('current','bin','ios-sim'),'showsdks'],aqua=True).check()
    assert b'6.1' in sdks.stdout+sdks.stderr
    usb=MAC.raw(['/usr/bin/python',MAC.path('current','usbmux-relay.py'),'--list']).json()
    assert isinstance(usb['devices'],list)
    # Positive protocol roundtrip through an actual SSH relay, then teardown.
    reserve=socket.socket(); reserve.bind(('127.0.0.1',0)); port=reserve.getsockname()[1]; reserve.close()
    remote_port=37000+int(uuid.uuid4().hex[:4],16)%20000
    tunnel=subprocess.Popen(MAC.usb_tunnel(port,remote_port),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+10
        connected=None
        while time.monotonic()<deadline:
            try:
                connected=socket.create_connection(('127.0.0.1',port),timeout=1)
                # A TCP forward can appear just before its remote listener.
                payload=plistlib.dumps({'MessageType':'ListDevices','ClientVersionString':'OpenReviveTest','ProgName':'OpenReviveTest'})
                connected.sendall(struct.pack('<IIII',len(payload)+16,1,8,1)+payload)
                def exact(count):
                    data=b''
                    while len(data)<count:
                        part=connected.recv(count-len(data))
                        if not part: raise OSError('Relay closed')
                        data+=part
                    return data
                size,version,message,tag=struct.unpack('<IIII',exact(16))
                assert 16<=size<1024*1024 and (version,message,tag)==(1,8,1)
                mux=plistlib.loads(exact(size-16))
                assert 'DeviceList' in mux
                break
            except OSError:
                if connected: connected.close()
                time.sleep(.15)
        else: raise AssertionError('USB relay did not become ready')
        connected.close()
    finally:
        tunnel.stdin.close()
        tunnel.wait(timeout=10)
        tunnel_stdout=tunnel.stdout.read(); tunnel_stderr=tunnel.stderr.read()
        assert tunnel.returncode==0 and b'USBMUX_RELAY_READY' in tunnel_stdout,(tunnel.returncode,tunnel_stdout,tunnel_stderr)
    try:
        leftover=socket.create_connection(('127.0.0.1',port),timeout=1); leftover.close()
    except OSError: pass
    else: raise AssertionError('USB tunnel left its local listener running')
    assert MAC.execute(['/usr/sbin/lsof','-nP','-iTCP:'+str(remote_port),'-sTCP:LISTEN']).code==1
    # Launch the new UIKit sample through the public CLI and verify app startup.
    app=MAC.path('work','build','iphonesimulator6.1','OpenReviveTouchProbe.app')
    launched=subprocess.run([str(ROOT/'bin/openrevive'),'sim','launch',app],capture_output=True)
    (OUT/'sim-launch.stdout').write_bytes(launched.stdout)
    (OUT/'sim-launch.stderr').write_bytes(launched.stderr)
    assert launched.returncode==0,(launched.returncode,launched.stderr)
    simulators=[a for a in MAC.ui('apps').json() if a['bundleId']=='com.apple.iphonesimulator']
    assert len(simulators)==1 and MAC.ui('info').json()['frontmostPid']==simulators[0]['pid']
    ready=False
    for _ in range(20):
        log_result=MAC.execute(['/bin/cat',MAC.path('work','logs','sim-stderr.log')])
        processes=MAC.execute(['/bin/ps','-axo','pid=,args=']).stdout
        if b'OPENREVIVE_TOUCH_READY' in log_result.stdout and b'OpenReviveTouchProbe.app/OpenReviveTouchProbe' in processes:
            ready=True; break
        time.sleep(.2)
    assert ready,'Simulator did not run the actual UIKit app'
    MAC.screenshot(OUT/'simulator.png')
    cleanup(root)
    (OUT/'workflow.json').write_text(json.dumps({'argumentRoundtrip':output,'usb':usb,'usbRelayRoundtrip':True,
        'simulatorLaunched':ready,'aquaCleanup':True,'sdkBuilds':['i386','armv7'],'xcodeBuilds':['i386','armv7']},ensure_ascii=False,indent=2))
    print(json.dumps({'usb':usb,'usbRelayRoundtrip':True,'sdkBuilds':['i386','armv7'],
        'xcodeBuilds':['i386','armv7'],'simulatorLaunched':ready,'aquaCleanup':True}))
    print('OPENREVIVE_WORKFLOW_OK')


if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('setup','gui','workflow'): raise SystemExit('Use setup | gui | workflow')
    globals()[sys.argv[1]]()
