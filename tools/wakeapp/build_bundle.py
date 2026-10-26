#!/usr/bin/env python3
"""Build the standalone utility on the release Mac. Never needed by end users."""
import argparse, hashlib, json, os, platform, re, shutil, subprocess, sys, tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def run(*argv):
    return subprocess.check_output(list(map(str,argv)),text=True,stderr=subprocess.STDOUT)

def build(output):
    if sys.platform!='darwin':raise SystemExit('Build this release on macOS.')
    output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    usb=output/'usb';usb.mkdir(exist_ok=True)
    sources={};queue=[Path(shutil.which(name)or'/missing/'+name)for name in ('iproxy','idevice_id','ideviceinfo')]
    while queue:
        source=queue.pop().resolve();name=source.name
        if name in sources:
            if sources[name]!=source:raise ValueError('Conflicting library name: '+name)
            continue
        sources[name]=source;shutil.copy2(source,usb/name);os.chmod(usb/name,0o755)
        dependencies=[]
        for line in run('otool','-L',source).splitlines()[1:]:
            dependency=line.strip().split(' (',1)[0]
            if dependency.startswith(('/usr/lib/','/System/Library/')):continue
            if dependency.startswith('@'):raise ValueError('Resolve non-system relative dependency before packaging: '+dependency)
            path=Path(dependency).resolve()
            if path==source:continue
            queue.append(path);dependencies.append((dependency,path.name))
        for old,new in dependencies:run('install_name_tool','-change',old,'@loader_path/'+new,usb/name)
        if name.endswith('.dylib'):run('install_name_tool','-id','@loader_path/'+name,usb/name)
    for name in sources:run('codesign','--force','--sign','-',usb/name)
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','wakeapp','--collect-data','certifi','--distpath',str(output/'dist'),'--workpath',str(output/'work'),'--specpath',str(output)]
    for name in sources:args+=['--add-binary',str(usb/name)+':usb']
    args.append(str(HERE/'cli.py'));subprocess.run(args,check=True)
    bundle=output/'dist/wakeapp'
    if not (HERE/'sources.json').is_file() or not (HERE/'licenses/Python-3.9.6.txt').is_file():raise ValueError('Collect release licenses and corresponding source first')
    shutil.copytree(HERE/'licenses',bundle/'licenses',dirs_exist_ok=True)
    shutil.copy2(HERE/'sources.json',bundle/'sources.json')
    shutil.copy2(HERE/'cli.py',bundle/'cli.py')
    shutil.copy2(HERE/'build_bundle.py',bundle/'build_bundle.py')
    for path in HERE.glob('LICENSE*'):shutil.copy2(path,bundle/path.name)
    if (HERE/'THIRD_PARTY.md').is_file():shutil.copy2(HERE/'THIRD_PARTY.md',bundle/'THIRD_PARTY.md')
    run(bundle/'wakeapp','--help')
    for name in ('iproxy','ideviceinfo','idevice_id'):
        linked=run('otool','-L',bundle/'_internal/usb'/name)
        if '/opt/homebrew/'in linked or '/usr/local/'in linked:raise ValueError('USB tool is not relocatable')
    receipt={'format':'wakeapp.macos-build/1','architecture':platform.machine(),'build_macos':platform.mac_ver()[0], 'python':sys.version,'source_sha256':hashlib.sha256((HERE/'cli.py').read_bytes()).hexdigest(),'usb_sources':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in sources.values()}}
    (bundle/'build.json').write_text(json.dumps(receipt,indent=2)+'\n')
    archive=output/('wakeapp-macos-'+platform.machine()+'.tar.gz')
    with tarfile.open(archive,'w:gz')as tar:tar.add(bundle,arcname='wakeapp')
    print(json.dumps({'file':str(archive),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);build(parser.parse_args().output)
