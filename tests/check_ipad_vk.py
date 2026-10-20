#!/usr/bin/env python3
"""Fresh, owner-bound original VK client steps on the explicit iPad lab target."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from check_ipad_live import IPad,ROOT,OUT,save
VK='com.vk.VKApp'


def brief(tree):
    return [{k:n.get(k) for k in ['id','class','role','label','value','secure','framePixels','actions']} for n in tree['nodes'] if n['visible'] and (n['label'] or n['role'] in ('text-field','text-view','table-cell','button'))]


def capture(p,name):
    tree,path=p.tree(name,bundle=VK);p.capture('vk-'+name)
    save('vk-'+name,tree);print(json.dumps({'context':{k:tree.get(k) for k in ['pid','screenPixels','scale','containerPath']},'nodes':brief(tree)},ensure_ascii=False));return tree,path


def select(tree,label,role=None,child=False):
    if child:
        by={n['id']:n for n in tree['nodes']};found={}
        for n in tree['nodes']:
            if not n['visible'] or label not in (n.get('label',''),n.get('value','')):continue
            while n['role']!=(role or 'table-cell') and n.get('parent') in by:n=by[n['parent']]
            if n['role']==(role or 'table-cell'):found[n['id']]=n
        matches=list(found.values())
    else:matches=[n for n in tree['nodes'] if n['visible'] and n['label']==label and (not role or n['role']==role)]
    assert len(matches)==1,(label,role,brief(tree));return matches[0]


def login(p):
    p.wake();p.launch(VK)
    tree,path=p.tree('login-check',bundle=VK)
    if not any(n['label']=='Email or login' and n['role']=='text-field' for n in tree['nodes']):
        capture(p,'already-authenticated');return
    for label,value in [('Email or login','openrevive-vk11'),('Password','openrevive-test')]:
        tree,path=p.tree('before-'+label.split()[0],bundle=VK);field=select(tree,label,'text-field')
        r=p.action('edit-'+label.split()[0],path,field,'set-text',value,expected=2 if label=='Password' else 0)
        if label=='Password':assert b'edit-not-retained' in r.stderr
        tree,path=p.tree('after-'+label.split()[0],bundle=VK);field=select(tree,label,'text-field')
        p.action('commit-'+label.split()[0],path,field,'return-key' if label=='Password' else 'end-editing')
    time.sleep(3)
    capture(p,'authenticated')


def classic_tap(p,name,label,role,child=False):
    # Explicit test fixture calibration, observed in a native 768x1024 image.
    # This is not a general automatic compatibility-mode coordinate mapper.
    tree,path=p.tree('classic-'+name,bundle=VK)
    assert tree['screenPixels']==[0,0,320,480]
    node=select(tree,label,role,child)
    image=p.capture('vk-before-'+name)
    from PIL import Image
    pixels=Image.open(image).convert('RGB')
    assert all(max(pixels.getpixel(point))<12 for point in [(100,500),(650,500),(384,200),(384,850)])
    # Require the measured 1x wrapper edges; a 2x image must fail this check.
    assert pixels.getpixel((204,500))!=pixels.getpixel((200,500))
    assert pixels.getpixel((562,500))!=pixels.getpixel((570,500))
    x,y,w,h=node['framePixels'];x+=w/2+224;y+=h/2+272
    assert 224<=x<544 and 272<=y<752
    definition={'fingers':[{'id':1,'points':[{'t':0,'x':x,'y':y},{'t':.08,'x':x,'y':y}]}]}
    file=OUT/('vk-physical-'+name+'.json');file.write_text(json.dumps(definition))
    dispatch=p.value('physical-'+name,'gesture',file,'--bundle',VK)
    time.sleep(.5);capture(p,name)
    save('vk-physical-'+name,{'node':node,'appScreen':tree['screenPixels'],'fixtureOffset':[224,272],'physicalPoint':[x,y],'dispatch':dispatch})


def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('phase',choices=['login','inspect','action','classic-tap']);a.add_argument('--udid',required=True);a.add_argument('--name',default='current');a.add_argument('--label');a.add_argument('--role');a.add_argument('--child',action='store_true');a.add_argument('--action',default='press');a.add_argument('--value');args=a.parse_args()
    p=IPad(args.udid,'vk-'+args.name)
    if args.phase=='login':login(p)
    elif args.phase=='inspect':capture(p,args.name)
    elif args.phase=='classic-tap':classic_tap(p,args.name,args.label,args.role,args.child)
    else:
        assert args.label
        tree,path=p.tree('action-'+args.name,bundle=VK);node=select(tree,args.label,args.role,args.child)
        p.action('action-'+args.name,path,node,args.action,args.value);time.sleep(.5);capture(p,args.name)
    print('OPENREVIVE_IPAD_VK_STEP_OK')


if __name__=='__main__':main()
