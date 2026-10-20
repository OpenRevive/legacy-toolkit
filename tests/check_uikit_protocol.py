#!/usr/bin/env python3
"""Validate device-independent UIKit ownership and multi-contact timing rules."""
import copy
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from iphone_uikit import compile_gesture,pinch_definition,action_request,act
from openrevive import parser
from openrevive_lib import Failure


def rejects(function,*args):
    try:function(*args)
    except Failure:return
    raise AssertionError('Invalid input was accepted: '+repr(args))


def validate_frames(packet,maximum):
    active=set();peak=0;last=-1
    for frame in packet['frames']:
        assert frame['t']>last;last=frame['t'];seen=set()
        for point in frame['contacts']:
            ident=point['id'];assert ident not in seen;seen.add(ident)
            assert 0<=point['x']<packet['width'] and 0<=point['y']<packet['height']
            if point['phase']=='down':assert ident not in active;active.add(ident)
            elif point['phase']=='up':assert ident in active;active.remove(ident)
            else:assert point['phase']=='move' and ident in active
        assert active<=seen;peak=max(peak,len(active))
    assert not active and peak==maximum


def main():
    seq={'fps':20,'fingers':[
        {'id':1,'points':[{'t':0,'x':100,'y':800},{'t':.1,'x':100,'y':800}]},
        {'id':2,'points':[{'t':.2,'x':500,'y':800},{'t':.3,'x':500,'y':800}]}]}
    validate_frames(compile_gesture(seq),1)
    overlap=copy.deepcopy(seq);overlap['fingers'][1]['points'][0]['t']=.05
    validate_frames(compile_gesture(overlap),2)
    pinch=pinch_definition(320,800,100,300,1)
    packet=compile_gesture(pinch);validate_frames(packet,2)
    first,last=packet['frames'][0]['contacts'],packet['frames'][-1]['contacts']
    assert math.isclose(abs(first[1]['x']-first[0]['x']),100)
    assert math.isclose(abs(last[1]['x']-last[0]['x']),300)
    vertical=compile_gesture(pinch_definition(320,600,80,200,.5,90));validate_frames(vertical,2)
    assert math.isclose(vertical['frames'][0]['contacts'][0]['x'],320)
    for bad in [None,{},[],{'fingers':[]},{'fingers':pinch['fingers'],'fps':True},{'fingers':pinch['fingers'],'fps':100}]:rejects(compile_gesture,bad)
    for field,value in [('x',-1),('x',640),('y',960),('t',float('nan')),('x',float('inf')),('t',True),('t',6)]:
        bad=copy.deepcopy(pinch);bad['fingers'][0]['points'][0][field]=value;rejects(compile_gesture,bad)
    bad=copy.deepcopy(pinch);bad['fingers'][1]['id']=1;rejects(compile_gesture,bad)
    bad=copy.deepcopy(pinch);bad['fingers'][0]['points'][1]['t']=0;rejects(compile_gesture,bad)
    bad=copy.deepcopy(pinch);bad['fingers'][0]['points'][1]['t']=.001;rejects(compile_gesture,bad)
    rejects(pinch_definition,10,800,100,300)
    rejects(pinch_definition,320,800,100,300,6)
    rejects(compile_gesture,pinch,960,640)
    ipad={'fingers':[{'id':1,'points':[{'t':0,'x':767,'y':1023},{'t':.1,'x':700,'y':1000}]}]}
    validate_frames(compile_gesture(ipad,768,1024),1)
    rejects(compile_gesture,ipad)  # The same coordinates must fail on iPhone.
    for key,value in [('x',768),('y',1024)]:
        bad=copy.deepcopy(ipad);bad['fingers'][0]['points'][0][key]=value
        rejects(compile_gesture,bad,768,1024)
    validate_frames(compile_gesture(pinch_definition(700,950,40,100,width=768,height=1024),768,1024),2)
    udid='a'*40
    snapshot={'udid':udid,'bundleId':'org.openrevive.Probe','pid':123,'snapshot':'snapshot-id','session':'process-session',
        'nodes':[{'id':'w0/v1','class':'UITextField','label':'Input','visible':True,'enabled':True,'actions':['set-text','focus','end-editing','resolve-tap']}]}
    request=action_request(snapshot,udid,'w0/v1','set-text','Привет 日本語')
    assert request['class']=='UITextField' and request['label']=='Input' and request['session']=='process-session' and request['pid']==123
    assert request['value']=='Привет 日本語' and 'auth' not in request
    rejects(action_request,snapshot,'b'*40,'w0/v1','focus')
    rejects(action_request,snapshot,udid,'missing','focus')
    rejects(action_request,snapshot,udid,'w0/v1','press')
    rejects(action_request,snapshot,udid,'w0/v1','focus','unexpected')
    assert action_request(snapshot,udid,'w0/v1','end-editing')['action']=='end-editing'
    rejects(action_request,snapshot,udid,'w0/v1','end-editing','unexpected')
    cli=parser().parse_args(['device','--udid',udid,'ui-action','unused.json','w0/v1','end-editing'])
    assert cli.action=='end-editing' and cli.value is None
    rejects(action_request,snapshot,udid,'w0/v1','set-text','😀'*3000)
    assert action_request(snapshot,udid,'w0/v1','tap')['action']=='resolve-tap'
    # A classic iPhone app's virtual rectangle must not be sent as iPad pixels.
    for screen,accepted in [([0,0,320,480],False),([0,0,768,1024],True)]:
        resolved={'screenPixels':screen,'framePixels':[10,20,40,30]}
        with patch('iphone_uikit.rpc',side_effect=[resolved,{'framebuffer':[768,1024]}]),patch('iphone_uikit.play',return_value={'ok':True}) as playback:
            if accepted:assert act(None,udid,snapshot,'w0/v1','tap')['ok']
            else:rejects(act,None,udid,snapshot,'w0/v1','tap')
            assert playback.call_count==int(accepted)
    for key,value in [('pid',-1),('pid',True),('session',123),('snapshot',[]),('bundleId','bad/path'),('nodes',None)]:
        bad=copy.deepcopy(snapshot);bad[key]=value;rejects(action_request,bad,udid,'w0/v1','focus')
    for key,value in [('class',None),('label',7),('actions',None),('actions',[{}])]:
        bad=copy.deepcopy(snapshot);bad['nodes'][0][key]=value;rejects(action_request,bad,udid,'w0/v1','focus')
    for field in ['visible','enabled']:
        bad=copy.deepcopy(snapshot);bad['nodes'][0][field]=False;rejects(action_request,bad,udid,'w0/v1','focus')
    bad=copy.deepcopy(snapshot);bad['nodes']*=2;rejects(action_request,bad,udid,'w0/v1','focus')
    print('OPENREVIVE_UIKIT_PROTOCOL_OK')


if __name__=='__main__':main()
