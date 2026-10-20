#!/usr/bin/env python3
"""Exercise actual RFB capture against cached, delayed, partial and stalled peers."""
import socket,struct,sys,tempfile,threading,time,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from iphone_vnc import VNC
from usbmux_bridge import exact

def update(rectangles):
 return b'\0\0'+struct.pack('>H',len(rectangles))+b''.join(struct.pack('>HHHHi',*r[:4],0)+r[4]for r in rectangles)
RED=b'\0\0\xff\0';GREEN=b'\0\xff\0\0'
def png_pixels(path):
 data=path.read_bytes();offset=8;compressed=b''
 while offset<len(data):
  size=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8];body=data[offset+8:offset+8+size]
  assert zlib.crc32(kind+body)&0xffffffff==struct.unpack('>I',data[offset+8+size:offset+12+size])[0]
  if kind==b'IDAT':compressed+=body
  offset+=size+12
 return zlib.decompress(compressed)
def peer(handler,output,**options):
 left,right=socket.socketpair();left.settimeout(1);right.settimeout(1)
 client=VNC.__new__(VNC);client.socket=left;client.width=2;client.height=1;errors=[]
 def run():
  try:
   with right:handler(right)
  except BaseException as e:errors.append(e)
 worker=threading.Thread(target=run);worker.start()
 try:return client.capture(output,**options)
 finally:
  client.close();worker.join(2);assert not worker.is_alive();assert not errors,errors

def main():
 with tempfile.TemporaryDirectory()as tmp:
  p=Path(tmp)/'capture.png';requests=[]
  def cached(sock):
   requests.append(time.monotonic());assert exact(sock,10)==struct.pack('>BBHHHH',3,0,0,0,2,1)
   sock.sendall(update([(0,0,2,1,RED*2)]));assert exact(sock,10)==struct.pack('>BBHHHH',3,0,0,0,2,1);requests.append(time.monotonic())
   sock.sendall(update([(0,0,2,1,GREEN*2)]))
  meta=peer(cached,p,settle=.08,timeout=1)
  assert requests[1]-requests[0]>=.07,requests
  assert meta['framesReceived']==2 and meta['changedDuringWarmup'] and meta['freshness']=='unverified'
  assert png_pixels(p)==b'\0\0\xff\0\0\xff\0'
  assert p.stat().st_mode&0o777==0o600
  # Static images are valid. Never mistake equal pixels for a frozen producer.
  def static(sock):
   for _ in range(2):exact(sock,10);sock.sendall(update([(0,0,2,1,RED*2)]))
  assert peer(static,p,settle=.01,timeout=1)['changedDuringWarmup']is False
  def tiled(sock):
   exact(sock,10);sock.sendall(b'\x02'+b'\x03\0\0\0'+struct.pack('>I',3)+b'abc'+update([])+update([(0,0,1,1,RED),(1,0,1,1,GREEN)]))
  peer(tiled,p,settle=0,timeout=1);assert png_pixels(p)==b'\0\xff\0\0\0\xff\0'
  original=p.read_bytes()
  def fail(handler,error,**options):
   try:peer(handler,p,**options)
   except error:pass
   else:raise AssertionError('Malformed or stalled frame was accepted')
   assert p.read_bytes()==original and not list(Path(tmp).glob('*.tmp'))
  def partial(sock):exact(sock,10);sock.sendall(update([(0,0,1,1,RED)]))
  fail(partial,RuntimeError,settle=0,timeout=1)
  def overlapping_hole(sock):exact(sock,10);sock.sendall(update([(0,0,1,1,RED),(0,0,1,1,GREEN)]))
  fail(overlapping_hole,RuntimeError,settle=0,timeout=1)
  def oversized(sock):exact(sock,10);sock.sendall(update([(0,0,3,1,RED*3)]))
  fail(oversized,RuntimeError,settle=0,timeout=1)
  def truncated(sock):exact(sock,10);sock.sendall(update([(0,0,2,1,RED*2)])[:-4])
  fail(truncated,RuntimeError,settle=0,timeout=1)
  def no_frame(sock):exact(sock,10);time.sleep(.2)
  started=time.monotonic();fail(no_frame,TimeoutError,settle=0,timeout=.08);assert time.monotonic()-started<.5
  def slow_header(sock):
   exact(sock,10)
   try:
    for byte in update([(0,0,2,1,RED*2)]):sock.sendall(bytes([byte]));time.sleep(.025)
   except (BrokenPipeError,ConnectionResetError):pass
  started=time.monotonic();fail(slow_header,TimeoutError,settle=0,timeout=.08);assert time.monotonic()-started<.5
  def missing_refresh(sock):
   exact(sock,10);sock.sendall(update([(0,0,2,1,RED*2)]));exact(sock,10);time.sleep(.2)
  fail(missing_refresh,TimeoutError,settle=.03,timeout=.1)
 print('OPENREVIVE_VNC_CAPTURE_VERIFIED')
if __name__=='__main__':main()
