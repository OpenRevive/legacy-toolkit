#!/usr/bin/env python3
"""Create only the dedicated small OpenRevive CT after checking its address."""
import json
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from infra import ART,STATE,ssh

def main():
    STATE.mkdir(parents=True,exist_ok=True)
    manifest=STATE/'origins.json'
    if manifest.exists():
        data=json.loads(manifest.read_text())
        existing=json.loads(ssh('proxmox',['pvesh','get','/cluster/resources','--type','vm','--output-format','json']))
        assert any(x['vmid']==data['ct']['vmid'] and x['name']=='openrevive-lab' for x in existing)
        print('OPENREVIVE_CT_ALREADY_ALLOCATED',data['ct']);return
    vmid=int(ssh('proxmox',['pvesh','get','/cluster/nextid']).strip())
    assert 100<=vmid<1000
    ip='192.168.0.129'
    probe='''import socket,struct,select,time
from pathlib import Path
interface='vmbr0';target=socket.inet_aton('192.168.0.129')
mac=bytes.fromhex(Path('/sys/class/net/'+interface+'/address').read_text().strip().replace(':',''))
packet=b'\\xff'*6+mac+b'\\x08\\x06'+struct.pack('!HHBBH',1,0x0800,6,4,1)+mac+b'\\x00'*4+b'\\x00'*6+target
s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0806));s.bind((interface,0));s.send(packet)
deadline=time.monotonic()+3
while time.monotonic()<deadline:
 if select.select([s],[],[],max(0,deadline-time.monotonic()))[0]:
  p=s.recv(2048)
  if len(p)>=42 and p[12:14]==b'\\x08\\x06' and p[28:32]==target:raise SystemExit('Address occupied by '+p[22:28].hex())
s.close();print('OPENREVIVE_CT_ADDRESS_FREE')
'''
    print(ssh('proxmox',['python3','-c',probe]).decode().strip())
    command=['pct','create',str(vmid),'local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst',
             '--hostname','openrevive-lab','--cores','1','--memory','256','--swap','128',
             '--rootfs','local-lvm:2','--unprivileged','1','--onboot','1','--start','1',
             '--net0',f'name=eth0,bridge=vmbr0,ip={ip}/24,gw=192.168.0.1,firewall=1',
             '--nameserver','1.1.1.1','--tags','openrevive;lab',
             '--description','OpenRevive isolated HTTP/HTTPS test origin; dedicated lab CT.']
    data={'ct':{'vmid':vmid,'name':'openrevive-lab','ipv4':ip,'domain':'ct.openrevive.test',
                'service':'openrevive-proxmox-lab','deployment_id':str(uuid.uuid4()),'anchor':'root-v2'},
          'vscale':{'name':'vscale','ipv4':'45.8.249.60','domain':'vscale.openrevive.test',
                    'service':'openrevive-vscale-lab','deployment_id':str(uuid.uuid4()),'anchor':'root-v2'}}
    manifest.write_text(json.dumps(data,indent=2)+'\n')
    # Persist the exact planned allocation before mutating; a retry cannot allocate a second VM.
    (ART/'ct-create.log').write_bytes(ssh('proxmox',command,timeout=180))
    print('OPENREVIVE_CT_ALLOCATED',data['ct'])

if __name__=='__main__':main()
