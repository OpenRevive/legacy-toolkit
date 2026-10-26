#!/usr/bin/env python3
"""Collect release notices and exact corresponding sources on the build Mac."""
import argparse, hashlib, json, re, shutil, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE=Path(__file__).resolve().parent

def prepare(receipt, output):
    output.mkdir(parents=True,exist_ok=True);licenses=HERE/'licenses';licenses.mkdir(exist_ok=True)
    roots={}
    for name in json.loads(receipt.read_text())['usb_sources']:
        p=Path(name);parts=p.parts;i=parts.index('Cellar');root=Path(*parts[:i+3]);roots[root.name+':'+root.parent.name]=root
    def collect(root):
        name=root.parent.name;formula=root/'.brew'/(name+'.rb');text=formula.read_text()
        url=re.search(r'^  url "([^"]+)"',text,re.M)[1]
        expected=re.search(r'^  sha256 "([a-f0-9]{64})"',text,re.M)[1]
        dest=output/url.rsplit('/',1)[1]
        if not dest.exists():
            request=urllib.request.Request(url,headers={'User-Agent':'OpenRevive-source-distribution'})
            dest.write_bytes(urllib.request.urlopen(request,timeout=90).read())
        actual=hashlib.sha256(dest.read_bytes()).hexdigest()
        if actual!=expected:raise ValueError('Source checksum mismatch: '+url)
        shutil.copy2(formula,output/(name+'.rb'))
        for path in root.iterdir():
            if path.is_file() and path.name.lower().startswith(('copying','license','authors','notice')):
                shutil.copy2(path,licenses/(name+'-'+path.name))
        return dict(name=name,version=root.name,file=dest.name,url=url,sha256=actual,recipe=name+'.rb')
    with ThreadPoolExecutor(max_workers=5) as pool:records=list(pool.map(collect,roots.values()))
    (output/'sources.json').write_text(json.dumps({'format':'wakeapp.corresponding-source/1','packages':records},indent=2)+'\n')
    shutil.copy2(output/'sources.json',HERE/'sources.json')
    print('Verified sources:',', '.join(r['name'] for r in records))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--receipt',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();prepare(a.receipt,a.output)
