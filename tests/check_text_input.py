#!/usr/bin/env python3
"""Exercise the shipped input bridge in a private iOS 6 Simulator app.

This does not install a device bridge, restart the physical phone, touch DNS,
or replace either existing OpenRevive probe application.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'plugins/openrevive/skills/legacy-mac'
REMOTE=SKILL/'scripts/remote'
OUT=ROOT/'.artifacts/text-input'
sys.path.insert(0,str(SKILL/'scripts'))
from openrevive_lib import Mac,configuration_path

SOURCES={'TextInputProbe.m':ROOT/'tests/fixtures/TextInputProbe.m',
         'legacy-uikit.m':REMOTE/'legacy-uikit.m','legacy-system.h':REMOTE/'legacy-system.h'}
REQUIRED={'unicodeAndSingleCallbacks','decorationChangesOnly','delegateCommitAndIdempotentEnd','explicitFocusAndBlink',
          'deleteAndDelegateVetoes','multilineInput','secureRedaction','staleMovedCoveredGuards'}
BUNDLE='org.openrevive.TextInputProbe'


def manifest():
    return {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in SOURCES.items()}


def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def validate(receipt):
    assert receipt['sources']==manifest(),'Native receipt belongs to older source'
    result=receipt['result']
    assert result.get('ok'),result
    assert all(result.get(key) is True for key in REQUIRED),result
    assert result['osVersion'].startswith('6.1') and 'Simulator' in result['model'],result
    assert result['realSelectionDecoration'] in ('UITextSelectionView','UICaretView')
    assert receipt['run_id']==result['runId']


def native():
    mac=Mac.from_config(configuration_path(),'blacklion')
    hashes=manifest();run_id=uuid.uuid4().hex
    remote=mac.path('work','tests','text-input-'+run_id[:12])
    before=mac.ui('info').json()
    with tempfile.TemporaryDirectory(prefix='openrevive-text-input-') as temporary:
        staging=Path(temporary)
        for name,path in SOURCES.items():(staging/name).write_bytes(path.read_bytes())
        mac.sync(staging,remote)
    developer=mac.execute(['/usr/bin/xcode-select','-print-path']).check().stdout.decode().strip()
    compiler=developer+'/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang'
    device_sdk=mac.execute(['/usr/bin/xcodebuild','-version','-sdk','iphoneos6.1','Path']).check().stdout.decode().strip()
    flags=['-Wall','-Wextra','-Werror','-Wno-unused-parameter']
    # Compile the exact production dylib for the actual iPhone ABI as well.
    compiled=mac.execute([compiler,*flags,'-arch','armv7','-isysroot',device_sdk,'-miphoneos-version-min=6.0',
        '-dynamiclib','-DOR_AUTH_TOKEN=@"compile-test-only"','-framework','UIKit','-framework','Foundation',
        '-framework','CoreFoundation','-framework','CoreGraphics',remote+'/legacy-uikit.m','-o',remote+'/UIKit-armv7.dylib'],timeout=60).check()
    (OUT/'compile-armv7.txt').write_bytes(compiled.stdout+compiled.stderr)
    sdk=mac.execute(['/usr/bin/xcodebuild','-version','-sdk','iphonesimulator6.1','Path']).check().stdout.decode().strip()
    app=remote+'/TextInputProbe.app'
    info={'CFBundleIdentifier':BUNDLE,'CFBundleExecutable':'TextInputProbe','CFBundleName':'TextInputProbe',
          'CFBundleDisplayName':'Input regression','CFBundleVersion':str(int(time.time())),
          'CFBundleShortVersionString':'1.0','CFBundlePackageType':'APPL','MinimumOSVersion':'6.0',
          'UIDeviceFamily':[1],'LSRequiresIPhoneOS':True,'CFBundleSupportedPlatforms':['iPhoneSimulator']}
    mac.python('import os,sys,base64;os.makedirs(sys.argv[1]);open(sys.argv[1]+"/Info.plist","wb").write(base64.b64decode(sys.argv[2]))',
               [app,base64.b64encode(plistlib.dumps(info)).decode()]).check()
    compiled=mac.execute([compiler,*flags,'-arch','i386','-isysroot',sdk,'-mios-simulator-version-min=6.0',
        '-DOR_TEST_RUN_ID=@"'+run_id+'"','-framework','UIKit','-framework','Foundation','-framework','CoreFoundation',
        '-framework','CoreGraphics','-framework','QuartzCore',remote+'/TextInputProbe.m','-o',app+'/TextInputProbe'],timeout=60).check()
    (OUT/'compile-simulator.txt').write_bytes(compiled.stdout+compiled.stderr)
    started=time.time()
    mac.execute([mac.path('current','bin','ios-sim'),'launch',app,'--sdk','6.1','--family','iphone','--exit','--timeout','30',
        '--stdout',remote+'/stdout.log','--stderr',remote+'/stderr.log'],timeout=35,aqua=True).check()
    search='''import os,sys,json,plistlib
root=os.path.expanduser('~/Library/Application Support/iPhone Simulator/6.1/Applications')
for name in os.listdir(root):
 p=os.path.join(root,name,'TextInputProbe.app','Info.plist')
 if os.path.isfile(p) and plistlib.readPlist(p).get('CFBundleIdentifier')==sys.argv[1]:
  f=os.path.join(root,name,'Documents','text-input-native.json')
  if os.path.isfile(f):
   data=json.load(open(f))
   if data.get('runId')==sys.argv[2]:print(json.dumps({'result':data,'directory':os.path.dirname(f)}));break
'''
    result=None
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        output=mac.python(search,[BUNDLE,run_id]).check().stdout.decode().strip()
        if output:result=json.loads(output);break
        time.sleep(.3)
    if result is None:
        log=mac.execute(['/bin/cat',remote+'/stderr.log']).check().stdout.decode(errors='replace')
        raise AssertionError('Native input probe did not finish: '+log[-5000:])
    receipt={'run_id':run_id,'started_at':started,'finished_at':time.time(),'sources':hashes,
             'armv7_compiled':True,'runtime':'iOS 6.1 Simulator','physical_phone_changed':False,'result':result['result']}
    save('native.json',receipt)
    mac.pull(result['directory']+'/text-input-native.png',OUT/'native.png')
    mac.pull(remote+'/stderr.log',OUT/'native-log.txt')
    # Stop only the private app whose process identity was returned by its own receipt.
    pid=result['result']['pid']
    current=mac.execute(['/bin/ps','-p',str(pid),'-o','comm=']).check().stdout.decode().strip()
    assert current.endswith('/TextInputProbe.app/TextInputProbe'),current
    mac.execute(['/bin/kill','-TERM',str(pid)]).check()
    after=mac.ui('info').json()
    front=next((app for app in mac.ui('apps').json() if app['pid']==after.get('frontmostPid')),None)
    if after.get('frontmostPid')!=before.get('frontmostPid') and front and front.get('bundleId')=='com.apple.iphonesimulator':
        mac.ui('focus',before['frontmostPid']).check()
    validate(receipt)
    print('OPENREVIVE_TEXT_INPUT_NATIVE_OK')


def local():
    commands=[['node','tools/mcp/build.mjs'],[sys.executable,'tests/check_uikit_protocol.py'],
              [sys.executable,'tests/check_package.py'],[sys.executable,'tests/check_mcp_backend.py'],
              ['node','tests/check_mcp_protocol.mjs']]
    checks=[]
    for index,command in enumerate(commands):
        result=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=90)
        log=result.stdout+result.stderr;(OUT/('local-'+str(index)+'.txt')).write_bytes(log)
        assert result.returncode==0,(command,result.returncode,log[-5000:].decode(errors='replace'))
        checks.append({'command':command,'exit_code':0,'output_sha256':hashlib.sha256(log).hexdigest()})
    save('local.json',checks);print('OPENREVIVE_TEXT_INPUT_LOCAL_OK')


def evidence():
    receipt=json.loads((OUT/'native.json').read_text());validate(receipt)
    assert receipt['armv7_compiled'] and receipt['physical_phone_changed'] is False
    for path in [SKILL/'SKILL.md',SKILL/'references/uikit.md',SKILL/'references/unified-ui.md',ROOT/'docs/text-input-2026-10-16.md']:
        assert 'end-editing' in path.read_text(),path
    assert (OUT/'native.png').read_bytes().startswith(bytes.fromhex('89504e470d0a1a0a'))
    assert len(json.loads((OUT/'local.json').read_text()))==5
    print('OPENREVIVE_TEXT_INPUT_EVIDENCE_OK')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['native','local','evidence']);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    globals()[args.mode]()
