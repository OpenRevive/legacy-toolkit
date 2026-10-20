#!/usr/bin/env python3
"""Fault-injected shared broker and real operation/stream regression checks."""
import contextlib
import fcntl
import copy
import ctypes as C
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
MCP=ROOT/'plugins/openrevive/mcp'
SCRIPTS=ROOT/'plugins/openrevive/skills/legacy-mac/scripts'
sys.path[:0]=[str(MCP),str(SCRIPTS)]
from operation_journal import Journal,JournalError,digest
from supervisor import Client,raw_request,runtime_root
from ui_operations import Operations
from ui_observation import Observations
from iphone_vnc import VNC
from device_web import Inspector
from png_pixels import pixels,cropped
UDID='a'*40


def rejected(function,*args):
    try:function(*args)
    except (RuntimeError,ValueError):return
    raise AssertionError('Expected refusal')


def journal():
    with tempfile.TemporaryDirectory(dir='/tmp')as tmp:
        path=Path(tmp)/'journal.sqlite3';j=Journal(path,epoch='a'*16)
        one=j.reserve('client','iphone:'+UDID)['operationId']
        args={'value':'secret-input','timeout':1,'guards':{'pid':7}}
        assert digest('ui_perform',{})==digest('ui_perform',{'action':'auto','mode':'perform','transition':'becomes_true','stableMs':100,'timeout':30,'maxDispatches':1,'nodeLimit':40,'byteLimit':12288})
        j.admit(one,'client','iphone:'+UDID,'ui_perform',args,123)
        assert not j.get(one)['executorQuiescent']
        j.event(one,'dispatch_intent')
        j.event(one,'remote_call',{'receipt':'/var/root/OpenRevive/'+'c'*32,'timeout':5})
        j.event(one,'remote_complete',{'receipt':'/var/root/OpenRevive/'+'c'*32})
        j.event(one,'native_targets',{'targets':[{'kind':'native','endpoint':'org.openrevive.hid'}]})
        j.event(one,'remote_call',{'receipt':'/var/root/OpenRevive/'+'d'*32,'timeout':5})
        j.finish(one,'VERIFICATION_TIMEOUT','dispatched',{'code':'VERIFICATION_TIMEOUT'})
        assert not j.admit(one,'client','iphone:'+UDID,'ui_perform',args,123)[1]
        rejected(j.admit,one,'client','iphone:'+UDID,'ui_perform',{'value':'different'},123)
        assert 'secret-input'not in json.dumps(j.get(one)['request_summary'])
        assert j.get(one)['receipts'][0]['completed']
        j.prune(0);rejected(j.get,one)
        two=j.reserve('client','iphone:'+UDID)['operationId']
        j.admit(two,'client','iphone:'+UDID,'ui_perform',{},123)
        j.event(two,'dispatch_intent');j.close()
        j=Journal(path,epoch='b'*16);assert j.recover()==[two]
        assert j.get(two)['code']=='OUTCOME_UNKNOWN'and not j.get(two)['executorQuiescent']
        assert j.device('iphone:'+UDID)['mode']=='reconciling'
        j.close()


FAKE='''import sys,json,os,time
count=0
closes=0
def emit(value):print(json.dumps(value),flush=True)
def checkpoint(phase,detail=None):
 emit({'checkpoint':{'phase':phase,'detail':detail or {}}})
 line=sys.stdin.readline()
 if not line:raise SystemExit()
 return json.loads(line).get('cancel')
for line in sys.stdin:
 r=json.loads(line);a=r['args'];m=r['method']
 if m=='ui_perform':
  if checkpoint('dispatch_intent'):emit({'result':{'code':'CANCELLED','effect':'none'}});continue
  checkpoint('remote_call',{'receipt':'/var/root/OpenRevive/'+'c'*32,'timeout':5})
  count+=1
  if a.get('crash'):os._exit(3)
  checkpoint('remote_complete',{'receipt':'/var/root/OpenRevive/'+'c'*32})
  checkpoint('dispatched')
  cancelled=False
  for _ in range(a.get('hold',0)):
   time.sleep(.02)
   if checkpoint('verifying'):cancelled=True;break
  if cancelled and a.get('cancel_loss'):emit({'error':'Lost remote acknowledgement while cancellation was pending'});continue
  emit({'result':{'code':'CANCELLED'if cancelled else'VERIFIED','effect':'dispatched','count':count}})
 elif m=='reconcile_execution':emit({'result':{'quiescent':True,'receipts':a['receipts']}})
 else:
  if m=='device_session'and a.get('operation')=='close':closes+=1
  emit({'result':{'pid':os.getpid(),'count':count,'method':m,'closes':closes}})
'''


def rpc():
    with tempfile.TemporaryDirectory(prefix='orv-',dir='/tmp')as tmp:
        root=Path(tmp);fake=root/'actor.py';fake.write_text(FAKE)
        env={'OPENREVIVE_SUPERVISOR_ROOT':str(root/'service'),'OPENREVIVE_SUPERVISOR_BACKEND':str(fake),'OPENREVIVE_SUPERVISOR_IDLE':'60'}
        with patch.dict(os.environ,env):
            # A previous server may still drain actors after closing its socket.
            # Startup waits for its singleton lock and retries a lost spawn race.
            service=runtime_root()
            with (service/'server.lock').open('a')as draining:
                fcntl.flock(draining,fcntl.LOCK_EX)
                startup={}
                thread=threading.Thread(target=lambda:startup.update(client=Client()))
                thread.start();time.sleep(.65);fcntl.flock(draining,fcntl.LOCK_UN)
                thread.join(5);assert not thread.is_alive()
            a,b=startup['client'],Client();where={'host':'fixture','udid':UDID}
            try:
                first=a.call('device_session',where)['result'];second=b.call('device_session',where)['result']
                assert first['pid']==second['pid']
                actor_pid=first['pid']
                identity=a.call('device_operation',{**where,'action':'reserve'})['result']['operationId']
                assert a.call('device_operation',{**where,'udid':'b'*40,'action':'status','operationId':identity})['error']=='OPERATION_DEVICE_MISMATCH'
                values={**where,'operationId':identity}
                result=a.call('ui_perform',values)['result'];assert result['count']==1
                assert a.call('ui_perform',values)['result']['deduplicated']
                assert a.call('ui_perform',{**values,'hold':2}).get('error')=='IDEMPOTENCY_CONFLICT'
                held={}
                thread=threading.Thread(target=lambda:held.update(a.call('ui_perform',{**where,'hold':100})));thread.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    busy=b.call('device_operation',{**where,'action':'health'})['result']['busy']
                    if busy and busy[0]['phase']=='verifying':break
                    time.sleep(.01)
                refusal=b.call('ui_perform',where)
                assert 'progress'in refusal['error']and refusal['owner']['consumer']==a.consumer
                assert b.call('read_logs',where)['result']['pid']!=actor_pid
                a.cancel();thread.join(4);assert not thread.is_alive()
                assert held['result']['operation']['code']=='CANCELLED'and held['result']['operation']['executorQuiescent']
                assert b.call('ui_perform',where)['result']['count']==3
                attached=Client();assert attached.call('device_session',{**where,'operation':'attach','sessionId':first['sessionId']})['result']['attached']
                assert attached.call('device_operation',{**where,'action':'status','operationId':identity})['result']['code']=='VERIFIED'
                a.call('device_session',{**where,'operation':'close','component':'all'})
                assert b.call('read_logs',where)['result']['closes']==1
                crashed=a.call('ui_perform',{**where,'crash':True});assert crashed['error']
                unknown=a.call('device_operation',{**where,'action':'list'})['result']['operations'][0]
                assert unknown['code']=='OUTCOME_UNKNOWN'and not unknown['executorQuiescent']
                assert b.call('ui_perform',where).get('error')
                reconciled=a.call('device_operation',{**where,'action':'reconcile','operationId':unknown['operationId']})['result']
                assert reconciled['executorQuiescent']and reconciled['code']=='OUTCOME_UNKNOWN'
                assert b.call('ui_perform',where)['result']['count']==1
                assert b.call('device_operation',{**where,'action':'handover'})['result']['manualControlReady']
                assert a.call('ui_perform',where).get('error')
                assert b.call('device_operation',{**where,'action':'resume'})['result']['freshObservationRequired']
                assert b.call('device_session',where)['result']['pid']!=actor_pid
                held={}
                thread=threading.Thread(target=lambda:held.update(a.call('ui_perform',{**where,'hold':100})));thread.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    busy=b.call('device_operation',{**where,'action':'health'})['result']['busy']
                    if busy and busy[0]['phase']=='verifying':break
                    time.sleep(.01)
                handover=b.call('device_operation',{**where,'action':'handover'})['result']
                assert not handover['manualControlReady']
                thread.join(4);assert held['result']['operation']['cancelRequested']
                assert b.call('device_operation',{**where,'action':'health'})['result']['device']['mode']=='manual'
                b.call('device_operation',{**where,'action':'resume'})
                held={}
                thread=threading.Thread(target=lambda:held.update(a.call('ui_perform',{**where,'hold':100,'cancel_loss':True})));thread.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    busy=b.call('device_operation',{**where,'action':'health'})['result']['busy']
                    if busy and busy[0]['phase']=='verifying':break
                    time.sleep(.01)
                a.cancel();thread.join(4);assert held['error']
                unknown=a.call('device_operation',{**where,'action':'list'})['result']['operations'][0]
                assert unknown['code']=='OUTCOME_UNKNOWN'and unknown['cancelRequested']and not unknown['executorQuiescent']
                assert b.call('ui_perform',where).get('error')
                assert a.call('device_operation',{**where,'action':'reconcile','operationId':unknown['operationId']})['result']['executorQuiescent']
            finally:
                raw_request(runtime_root(),{'method':'_shutdown'})
                deadline=time.monotonic()+6
                while (runtime_root()/'control.sock').exists()and time.monotonic()<deadline:time.sleep(.05)


class UI:
    def __init__(self):
        self.execution_effect='none';self.budget_checker=None;self.calls=0;self.sequence=0;self.items={};self.text='Old';self.saved=False;self.offset=0
    def checkpoint(self,*args):
        if self.budget_checker:self.budget_checker()
    def observe(self,a):
        self.sequence+=1;ref=str(self.sequence)
        nodes=[{'id':'root','label':'Form','class':'UIView','visible':True},
            {'id':'save','parent':'root','label':'Save','identifier':'save','role':'button','actionable':True,'enabled':True},
            {'id':'title','parent':'root','label':'Title','identifier':'title','role':'text-field','actionable':True,'value':self.text},
            {'id':'status','parent':'root','label':'Status','identifier':'status','value':'Saved'if self.saved else'Unsaved'},
            {'id':'scroll','parent':'root','label':'List','identifier':'list','actionable':True,'contentOffsetPoints':[0,self.offset],'scrollRangePoints':[0,0,0,400]}]
        if self.offset>=180:nodes.append({'id':'target','parent':'root','label':'Target','identifier':'target','actionable':True})
        tree={'nodes':nodes,'systemContext':{'frontmostBundleId':'org.example.fixture','frontmostPid':7,'surface':'application','screenLocked':False},'coverage':{'completeForKnownScreenOwners':True}}
        self.items[ref]={'tree':tree};return {'snapshotId':ref,'revision':ref,'nodes':nodes}
    def get(self,ref):return self.items[ref]
    def _dispatch(self,method,a):
        assert method=='act_ui';self.calls+=1;self.execution_effect='dispatched'
        if a['nodeId']=='title':self.text=a['value']
        if a['nodeId']=='save':self.saved=True
        if a['nodeId']=='scroll':self.offset=a['value'][1]
        return {'route':'fixture-native','posted':True}


def operations():
    base={'udid':UDID,'bundle':'org.example.fixture','timeout':1,'stableMs':0}
    expect={'state':'value','selector':{'identifier':'status'},'value':'Saved'}
    worker=UI();args={**base,'selector':{'identifier':'save','within':{'label':'Form'}},'expect':expect,'action':'press'}
    result=Operations(worker,args).perform(args);assert result['code']=='VERIFIED'and worker.calls==1
    worker.execution_effect='none'
    result=Operations(worker,{**args,'mode':'ensure'}).perform({**args,'mode':'ensure'})
    assert result['code']=='ALREADY_SATISFIED'and worker.calls==1 and result['effect']=='none'
    worker=UI();wrong={**args,'bundle':'wrong.bundle'}
    assert Operations(worker,wrong).perform(wrong)['code']=='CONTEXT_CHANGED'and not worker.calls
    worker=UI();workflow={**base,'steps':[
        {'kind':'perform','selector':{'identifier':'title'},'action':'set-text','value':'日本語','expect':{'state':'value','selector':{'identifier':'title'},'value':'日本語'},'stableMs':0},
        {'kind':'perform','selector':{'identifier':'save'},'action':'press','expect':expect,'stableMs':0}]}
    result=Operations(worker,workflow).workflow(workflow)
    assert result['code']=='VERIFIED'and worker.calls==2 and len(result['completedSteps'])==2
    worker=UI();workflow={**base,'steps':[{'kind':'scroll_find','selector':{'identifier':'target'},'container':{'identifier':'list'}}]}
    result=Operations(worker,workflow).workflow(workflow);assert result['code']=='VERIFIED'and worker.calls==1
    worker=UI();impossible={**args,'timeout':.12,'expect':{'state':'value','selector':{'identifier':'status'},'value':'Never'}}
    result=Operations(worker,impossible).perform(impossible)
    assert result['code']in('BUDGET_EXCEEDED','VERIFICATION_TIMEOUT')and worker.calls==1 and result['effect']=='dispatched'
    class Transition(UI):
        reads=0
        def _dispatch(self,method,args):
            assert method=='iphone_input';self.calls+=1;self.execution_effect='dispatched'
            return {'posted':True,'route':'fixture-launch'}
        def observe(self,args):
            self.reads+=1
            if self.reads==1:raise RuntimeError('Device bridge screen-changed: Screen owner changed during capture')
            return super().observe(args)
    worker=Transition();workflow={**base,'steps':[{'kind':'ensure_context','bundle':base['bundle']}]}
    result=Operations(worker,workflow).workflow(workflow)
    assert result['code']=='VERIFIED'and worker.calls==1 and worker.reads==2
    assert result['completedSteps'][0]['result']['transitionReadRetries']==1


def web():
    inspector=Inspector.__new__(Inspector)
    inspector.connection='connection';inspector.contexts={};inspector.documents={};inspector.page_events=set()
    inspector.references={'page':{'app':'com.apple.mobilesafari','page':1,'connection':''}}
    inspector.attached={('com.apple.mobilesafari',1):'sender'}
    current={'loader':'one'}
    inspector.command=lambda ref,method,*args: {'frameTree':{'frame':{'id':'main','loaderId':current['loader'],'url':'https://same.test/'}}}if method=='Page.getResourceTree'else{}
    a=inspector.bind_context('page',{'bundle':'com.apple.mobilesafari'})
    assert inspector.bind_context('page',context_id=a['contextId'])['documentEpoch']==a['documentEpoch']
    current['loader']='two';rejected(inspector.bind_context,'page',None,a['contextId'])
    b=inspector.bind_context('page')
    inspector.consume({'__selector':'_rpc_applicationSentData:','__argument':{'WIRDestinationKey':'sender','WIRMessageDataKey':json.dumps({'method':'DOM.documentUpdated'}).encode()}})
    rejected(inspector.context_page,b['contextId'])
    inspector.attached={}


def incremental():
    from usbmux_bridge import exact
    left,right=socket.socketpair();left.settimeout(1);right.settimeout(1)
    client=VNC.__new__(VNC);client.socket=left;client.width=2;client.height=1
    errors=[];done=threading.Event()
    def update(x,w,color):return b'\0\0\0\1'+struct.pack('>HHHHi',x,0,w,1,0)+color*w
    def peer():
        try:
            assert exact(right,10)[1]==0;right.sendall(update(0,2,b'\0\0\xff\0'))
            assert exact(right,10)[1]==1
            message=update(0,1,b'\0\xff\0\0');right.sendall(message[:2]);time.sleep(.08)
            right.sendall(message[2:])
            assert exact(right,10)[1]==1
            done.wait(1)
        except Exception as error:errors.append(error)
        finally:right.close()
    thread=threading.Thread(target=peer);thread.start()
    try:
        with tempfile.TemporaryDirectory(dir='/tmp')as tmp:
            out=Path(tmp)/'screen.png'
            one=client.capture(out,settle=0)
            two=client.capture(out,update_timeout=.02)
            assert two['cached']and two['pendingUpdate']and two['partialWireBytes']==2
            three=client.capture(out,update_timeout=.3)
            assert three['framesReceived']==1 and three['pixelSha256']!=one['pixelSha256']
            four=client.capture(out,update_timeout=.02,crop=[1,0,1,1])
            assert four['cached']and four['frameReceivedAt']==three['frameReceivedAt']and four['width']==1
    finally:done.set();client.close();thread.join(2)
    assert not errors,errors


def native_crop():
    if sys.platform!='darwin':return
    from image_artifacts import crop_png
    import zlib
    with tempfile.TemporaryDirectory()as tmp:
        source=Path(tmp)/'source.png';output=Path(tmp)/'cropped.png'
        w,h=16,12
        rgb=bytes(v for y in range(h)for x in range(w)for v in (x*11,y*17,(x+y)*7))
        raw=b''.join(b'\0'+rgb[y*w*3:(y+1)*w*3]for y in range(h))
        def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
        source.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))
        for region in ([0,0,4,3],[5,2,7,6],[12,9,4,3]):
            report=crop_png(source,output,region)
            cw,ch,actual=pixels(output.read_bytes())
            assert (cw,ch)==tuple(region[2:])and actual==cropped(rgb,w,region)
            assert report['imageOrigin']==region[:2]
        original=output.read_bytes()
        rejected(crop_png,source,output,[15,0,2,1]);assert output.read_bytes()==original


GROUPS={'journal':journal,'rpc':rpc,'operations':operations,'web':web,'incremental':incremental,'native_crop':native_crop}
if __name__=='__main__':
    for name in sys.argv[1:]or GROUPS:
        GROUPS[name]();print('OPENREVIVE_SUPERVISOR_'+name.upper()+'_OK',flush=True)
