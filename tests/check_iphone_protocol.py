#!/usr/bin/env python3
"""Protocol regressions for Lion pairing compatibility and RFB input/capture."""
import json
import importlib.util
import os
from pathlib import Path
import plistlib
import socket
import struct
import sys
import tempfile
import threading
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from usbmux_bridge import Bridge, PairStore, exact, packet
from iphone_vnc import response, VNC
from iphone_setup import ar_members, packages


def send(connection,value,tag=23):
    data=plistlib.dumps(value)
    connection.sendall(struct.pack('<IIII',len(data)+16,1,8,tag)+data)
    metadata,raw,payload=packet(connection)
    assert metadata==(1,8,tag)
    return plistlib.loads(payload)


def main():
    spec=importlib.util.spec_from_file_location('usb_inventory',ROOT/'plugins/openrevive/skills/legacy-mac/scripts/remote/usb-inventory.py')
    inventory=importlib.util.module_from_spec(spec);spec.loader.exec_module(inventory)
    # Actual Lion SPUSBDataType layout: schema metadata also contains a dict
    # named _name, and USB field names have alphabetical prefixes.
    fixture=[{'_properties':{'_name':{'_order':'0'}},'_items':[{'_name':'iPhone',
        'd_serial_num':'A'*40,'a_product_id':'0x1297','b_vendor_id':'apple_vendor_id','e_device_speed':'high_speed'}]}]
    assert inventory.parse(fixture)==[{'name':'iPhone','serial':'A'*40,'productId':'0x1297','vendorId':'apple_vendor_id','speed':'high_speed'}]
    with tempfile.TemporaryDirectory() as directory:
        store=PairStore(Path(directory)/'pairs')
        assert store.handle({'MessageType':'ReadBUID'})==PairStore(Path(directory)/'pairs').handle({'MessageType':'ReadBUID'})
        assert (Path(directory)/'pairs').stat().st_mode&0o777==0o700
        record=plistlib.dumps({'HostID':'test','HostCertificate':b'fixture'})
        assert store.handle({'MessageType':'SavePairRecord','PairRecordID':'../escape','PairRecordData':record})['Number']!=0
        assert store.handle({'MessageType':'SavePairRecord','PairRecordID':'ABC','PairRecordData':record})['Number']==0
        assert store.handle({'MessageType':'ReadPairRecord','PairRecordID':'ABC'})['PairRecordData']==record
        assert (Path(directory)/'pairs/ABC.plist').stat().st_mode&0o777==0o600
        assert store.handle({'MessageType':'SavePairRecord','PairRecordID':'ABC','PairRecordData':b'x'*(1024*1024+1)})['Number']!=0
        # A real socket relay must intercept metadata locally while leaving the
        # post-Connect binary channel unchanged, including NUL and non-UTF8.
        backend=socket.socket();backend.bind(('127.0.0.1',0));backend.listen()
        errors=[]
        def upstream():
            try:
                connection,_=backend.accept()
                with connection:
                    meta,raw,payload=packet(connection)
                    assert plistlib.loads(payload)['MessageType']=='Connect'
                    value=plistlib.dumps({'MessageType':'Result','Number':0})
                    connection.sendall(struct.pack('<IIII',len(value)+16,1,8,meta[2])+value)
                    assert exact(connection,4)==b'\0\xff\x80x';connection.sendall(b'y\x80\xff\0')
            except BaseException as error: errors.append(error)
        worker=threading.Thread(target=upstream,daemon=True);worker.start()
        with Bridge(0,backend.getsockname(),Path(directory)/'pairs') as bridge:
            thread=threading.Thread(target=bridge.serve_forever,daemon=True);thread.start()
            try:
                with socket.create_connection(bridge.server_address,timeout=3) as connection:
                    assert send(connection,{'MessageType':'ReadPairRecord','PairRecordID':'ABC'})['PairRecordData']==record
                with socket.create_connection(bridge.server_address,timeout=3) as connection:
                    assert send(connection,{'MessageType':'Connect','DeviceID':1,'PortNumber':1234})['Number']==0
                    connection.sendall(b'\0\xff\x80x');assert exact(connection,4)==b'y\x80\xff\0'
            finally: bridge.shutdown();thread.join(timeout=2)
        worker.join(timeout=3);backend.close();assert not worker.is_alive() and not errors,errors
        store.handle({'MessageType':'DeletePairRecord','PairRecordID':'ABC'})
        assert store.handle({'MessageType':'ReadPairRecord','PairRecordID':'ABC'})['Number']!=0
        # Standard DES known-answer vector, encoded using VNC's reversed key bits.
        key=bytes.fromhex('133457799bbcdff1')
        password=bytes(int('{:08b}'.format(x)[::-1],2) for x in key).decode('latin-1')
        assert response(password,bytes.fromhex('0123456789abcdef')*2)==bytes.fromhex('85e813540f0ab405')*2
        left,right=socket.socketpair();left.settimeout(3);right.settimeout(3)
        vnc=VNC.__new__(VNC);vnc.socket=left;vnc.width=2;vnc.height=1
        # Two RGB pixels (red, green), delivered as little-endian 32-bit BGRX.
        right.sendall(b'\0\0\0\1'+struct.pack('>HHHHi',0,0,2,1,0)+b'\0\0\xff\0\0\xff\0\0')
        image=Path(directory)/'capture.png';vnc.capture(image,settle=0)
        data=image.read_bytes();offset=8;compressed=b''
        while offset<len(data):
            length=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8];body=data[offset+8:offset+8+length]
            assert zlib.crc32(kind+body)&0xffffffff==struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
            if kind==b'IDAT':compressed+=body
            offset+=12+length
        assert zlib.decompress(compressed)==b'\0\xff\0\0\0\xff\0'
        try:vnc.pointer(2,0)
        except ValueError:pass
        else:raise AssertionError('Out of bounds pointer accepted')
        left.close();right.close()
    records=packages();assert len(records)==9 and len({r['package'] for r in records})==9
    assert next(r for r in records if r['package']=='preferenceloader')['version']=='2.2.6'
    for r in records:assert len(r['sha256'])==64 and r['bytes']>0
    assert dict(ar_members(b'!<arch>\n'))=={}
    print('OPENREVIVE_IPHONE_PROTOCOL_OK')


if __name__=='__main__':main()
