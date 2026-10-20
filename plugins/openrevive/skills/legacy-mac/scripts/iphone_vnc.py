"""Bounded RFB input/screens through usbmuxd, with Veency password auth."""
import os
import hashlib
from pathlib import Path
import plistlib
import socket
import struct
import time
import tempfile
import zlib

from usbmux_bridge import exact, packet


def request(connection, value, tag=1):
    data = plistlib.dumps(value)
    connection.sendall(struct.pack('<IIII', len(data)+16, 1, 8, tag)+data)
    return plistlib.loads(packet(connection)[2])


def connect(udid, port, address):
    host, muxport = address.rsplit(':', 1)
    if host != '127.0.0.1': raise ValueError('Device transport must use the loopback SSH bridge')
    connection = socket.create_connection((host, int(muxport)), timeout=10)
    try:
        value = request(connection, {'MessageType':'ListDevices', 'ProgName':'OpenRevive'})
        matches = [x for x in value.get('DeviceList', []) if x.get('Properties',{}).get('SerialNumber') == udid]
        if len(matches) != 1: raise RuntimeError('Explicit UDID is not currently connected')
    finally: connection.close()
    connection = socket.create_connection((host,int(muxport)),timeout=10)
    result = request(connection, {'MessageType':'Connect','DeviceID':matches[0]['DeviceID'],
        'PortNumber':socket.htons(port),'ProgName':'OpenRevive'})
    if result.get('Number') != 0:
        connection.close(); raise RuntimeError('Device port %s refused: %r' % (port,result))
    return connection


def response(password, challenge):
    # VNC uses reversed-bit DES. TripleDES with a repeated single key has exactly
    # the required DES behavior. cryptography is optional outside VNC commands.
    try: from cryptography.hazmat.primitives.ciphers import Cipher, modes
    except ImportError as error: raise RuntimeError('VNC password authentication needs the controller Python package cryptography; install it in the Python used for this command') from error
    try: from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError: from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    raw = password.encode('latin-1')[:8].ljust(8,b'\0')
    key = bytes(int('{:08b}'.format(x)[::-1],2) for x in raw)
    encryptor = Cipher(TripleDES(key*3),modes.ECB()).encryptor()
    return encryptor.update(challenge)+encryptor.finalize()


class VNC:
    def __init__(self, udid, password, address):
        self.socket = connect(udid,5900,address)
        try:
            banner = exact(self.socket,12)
            if not banner.startswith(b'RFB 003.'): raise RuntimeError('Unexpected VNC banner')
            modern = int(banner[8:11]) >= 7
            self.socket.sendall(b'RFB 003.008\n' if modern else b'RFB 003.003\n')
            if modern:
                count = exact(self.socket,1)[0]
                types = exact(self.socket,count)
                if 2 not in types: raise RuntimeError('Veency must have a nonempty password; refusing unauthenticated input')
                self.socket.sendall(b'\x02')
            elif struct.unpack('>I',exact(self.socket,4))[0] != 2:
                raise RuntimeError('VNC password authentication required')
            self.socket.sendall(response(password,exact(self.socket,16)))
            if struct.unpack('>I',exact(self.socket,4))[0] != 0: raise RuntimeError('VNC authentication failed')
            self.socket.sendall(b'\x01')
            header = exact(self.socket,24)
            self.width,self.height = struct.unpack('>HH',header[:4])
            length = struct.unpack('>I',header[20:])[0]
            if length > 4096 or not 1 <= self.width*self.height <= 16*1024*1024: raise RuntimeError('Invalid VNC display')
            self.name = exact(self.socket,length).decode(errors='replace')
            pixels = struct.pack('>BBBBHHHBBBxxx',32,24,0,1,255,255,255,16,8,0)
            self.socket.sendall(b'\0\0\0\0'+pixels)
            # Veency's libvncserver can choose zlib, with raw as a fallback.
            self.socket.sendall(struct.pack('>BBHii',2,0,2,6,0))
            self.capture_ready = False
        except EOFError as error:
            self.socket.close()
            raise RuntimeError('VNC disconnected during handshake') from error
        except Exception:
            self.socket.close(); raise

    def pointer(self,x,y,mask=0):
        if not 0 <= x < self.width or not 0 <= y < self.height: raise ValueError('Point outside current framebuffer')
        self.socket.sendall(struct.pack('>BBHH',5,mask,x,y))

    def click(self,x,y,button=1):
        self.pointer(x,y,button); time.sleep(.08); self.pointer(x,y,0); time.sleep(.12)

    def swipe(self,x1,y1,x2,y2,duration=.5):
        if not .05 <= duration <= 5: raise ValueError('Swipe duration must be .05..5 seconds')
        self.pointer(x1,y1,1)
        try:
            for i in range(1,21):
                time.sleep(duration/20); self.pointer(round(x1+(x2-x1)*i/20),round(y1+(y2-y1)*i/20),1)
        finally: self.pointer(x2,y2,0)
        time.sleep(.15)

    def key(self,keysym):
        for down in (1,0): self.socket.sendall(struct.pack('>BBHI',4,down,0,keysym))
        time.sleep(.04)

    def type(self,text):
        for char in text:
            value = ord(char)
            self.key(value if value <= 255 else 0x01000000|value)

    def _frame(self, deadline, incremental=False):
        """Keep partial wire messages across ordinary incremental timeouts."""
        if not 1 <= self.width*self.height <= 16*1024*1024:
            raise RuntimeError('Invalid VNC framebuffer dimensions')
        if not hasattr(self, 'rfb_buffer'): self.rfb_buffer = bytearray()
        pending = getattr(self, 'update_pending', False)
        if not pending or (not incremental and not getattr(self, 'want_full', False)):
            self.socket.sendall(struct.pack('>BBHHHH',3,int(incremental),0,0,self.width,self.height))
            self.request_wire_bytes = getattr(self,'request_wire_bytes',0)+10
            self.prior_partial = pending
            self.want_full = not incremental
            self.update_pending = True
        buffer = self.rfb_buffer
        messages = 0
        while time.monotonic() < deadline:
            while buffer:
                messages += 1
                if messages > 4096: raise RuntimeError('Excessive VNC server messages')
                kind = buffer[0]
                if kind == 2: del buffer[:1]; continue
                if kind == 3:
                    if len(buffer)<8: break
                    size=struct.unpack('>I',buffer[4:8])[0]
                    if size>1024*1024: raise RuntimeError('Oversized VNC clipboard')
                    if len(buffer)<8+size: break
                    del buffer[:8+size]; continue
                if kind != 0: raise RuntimeError('Unexpected VNC server message')
                if len(buffer)<4: break
                count=struct.unpack('>H',buffer[2:4])[0]
                if count>4096: raise RuntimeError('Excessive VNC rectangles')
                offset, rectangles, pixels, complete = 4, [], 0, True
                for _ in range(count):
                    if len(buffer)<offset+12: complete=False; break
                    x,y,w,h,encoding=struct.unpack('>HHHHi',buffer[offset:offset+12]);offset+=12
                    if encoding not in (0,6) or not w or not h or x+w>self.width or y+h>self.height:
                        raise RuntimeError('Unsupported VNC framebuffer rectangle')
                    pixels+=w*h
                    if pixels>self.width*self.height*2: raise RuntimeError('Oversized framebuffer update')
                    if encoding==6:
                        if len(buffer)<offset+4: complete=False;break
                        size=struct.unpack('>I',buffer[offset:offset+4])[0];offset+=4
                        if not 0<size<=self.width*self.height*4+65536: raise RuntimeError('Oversized compressed rectangle')
                    else:size=w*h*4
                    if len(buffer)<offset+size:complete=False;break
                    rectangles.append((x,y,w,h,encoding,bytes(buffer[offset:offset+size])));offset+=size
                if not complete: break
                del buffer[:offset]
                if not count:
                    if getattr(self,'want_full',False):continue
                    self.update_pending=False
                    return None
                coverage=[[] for _ in range(self.height)]
                framebuffer=bytearray(getattr(self,'framebuffer',bytes(self.width*self.height*3)))
                for x,y,w,h,encoding,data in rectangles:
                    if encoding==6:
                        inflater=getattr(self,'inflater',None)
                        if inflater is None or inflater.eof:self.inflater=inflater=zlib.decompressobj()
                        try:data=inflater.decompress(data,w*h*4+1)
                        except zlib.error as error:raise RuntimeError('Invalid VNC zlib rectangle') from error
                        if len(data)!=w*h*4 or inflater.unconsumed_tail or inflater.unused_data:
                            raise RuntimeError('Invalid VNC decompressed rectangle size')
                    for row in range(h):
                        coverage[y+row].append((x,x+w))
                        raw=data[row*w*4:(row+1)*w*4];rgb=bytearray(w*3)
                        rgb[0::3],rgb[1::3],rgb[2::3]=raw[2::4],raw[1::4],raw[0::4]
                        at=((y+row)*self.width+x)*3;framebuffer[at:at+w*3]=rgb
                full=True
                for intervals in coverage:
                    right=0
                    for left,end in sorted(intervals):
                        if left>right:full=False;break
                        right=max(right,end)
                    if right!=self.width:full=False
                if not full and not hasattr(self,'framebuffer'):
                    raise RuntimeError('Incomplete VNC framebuffer update')
                if self.want_full and not full and not getattr(self,'prior_partial',False):
                    raise RuntimeError('Incomplete VNC framebuffer update')
                self.framebuffer=framebuffer
                self.frame_received_at=time.time()
                if self.want_full and not full:continue
                self.update_pending=False;self.want_full=False
                return framebuffer
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            self.socket.settimeout(remaining)
            try:chunk=self.socket.recv(65536)
            except socket.timeout:return None
            if not chunk:raise RuntimeError('VNC connection closed during capture')
            buffer.extend(chunk)
            self.capture_wire_bytes=getattr(self,'capture_wire_bytes',0)+len(chunk)
            if len(buffer)>self.width*self.height*8+1024*1024:raise RuntimeError('Oversized VNC receive buffer')
        return None

    def capture(self, output, *, settle=2.0, timeout=8.0, incremental=True, update_timeout=.75, crop=None):
        """Warm once; retain cache and framing when an unchanged screen is silent."""
        if not 0<=settle<=5 or not 0<timeout<=30 or timeout<=settle or not .01<=update_timeout<=5:
            raise ValueError('Invalid VNC capture timing')
        started=time.monotonic();deadline=started+timeout
        original_timeout=self.socket.gettimeout()
        self.capture_wire_bytes=self.request_wire_bytes=0
        warming=not getattr(self,'capture_ready',False)
        frames=0;first_hash=None
        try:
            if warming:
                framebuffer=self._frame(deadline,False)
                if framebuffer is None:raise TimeoutError('VNC initial capture deadline exceeded')
                frames+=1;first_hash=hashlib.sha256(framebuffer).hexdigest()
                if settle:
                    remaining=started+settle-time.monotonic()
                    if remaining>0:time.sleep(remaining)
                    framebuffer=self._frame(deadline,False)
                    if framebuffer is None:raise TimeoutError('VNC warmup refresh deadline exceeded')
                    frames+=1
                self.capture_ready=True
            else:
                frame=self._frame(min(deadline,started+update_timeout) if incremental else deadline,incremental)
                if frame is not None:frames+=1
                elif not incremental:raise TimeoutError('VNC full-frame deadline exceeded')
                framebuffer=self.framebuffer
        except (TimeoutError,EOFError,OSError,RuntimeError):
            self.socket.close();raise
        finally:
            if self.socket.fileno()>=0:self.socket.settimeout(original_timeout)
        pixel_hash=hashlib.sha256(framebuffer).hexdigest()
        width,height=self.width,self.height
        image=framebuffer
        origin=[0,0]
        if crop is not None:
            if not isinstance(crop,(list,tuple)) or len(crop)!=4 or any(type(v)is not int for v in crop):
                raise ValueError('Crop must be integer [x,y,width,height]')
            x,y,width,height=crop
            if min(x,y)<0 or min(width,height)<1 or x+width>self.width or y+height>self.height:
                raise ValueError('Crop outside VNC framebuffer')
            image=b''.join(framebuffer[((y+r)*self.width+x)*3:((y+r)*self.width+x+width)*3] for r in range(height))
            origin=[x,y]
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        raw=b''.join(b'\0'+image[y*width*3:(y+1)*width*3] for y in range(height))
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
        path=Path(output).expanduser().resolve()
        fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',suffix='.tmp',dir=path.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb')as stream:stream.write(png)
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()
        return {'path':str(path),'width':width,'height':height,'screenWidth':self.width,'screenHeight':self.height,
            'imageOrigin':origin,'crop':list(crop)if crop else None,'bytes':len(png),'transport':'vnc',
            'framesReceived':frames,'warmupSeconds':settle if warming else 0,'incremental':bool(incremental and not warming),
            'cacheStatus':'updated'if frames else 'no-update-before-deadline','cached':not bool(frames),
            'pendingUpdate':getattr(self,'update_pending',False),'partialWireBytes':len(getattr(self,'rfb_buffer',b'')),
            'elapsedMs':round((time.monotonic()-started)*1000),'pixelSha256':pixel_hash,
            'wireBytes':self.capture_wire_bytes,'requestWireBytes':self.request_wire_bytes,'supportedEncodings':['zlib','raw'],
            'changedDuringWarmup':first_hash!=pixel_hash if warming else False,
            'frameReceivedAt':self.frame_received_at,'receivedAt':self.frame_received_at,'returnedAt':time.time(),
            'ageMs':round((time.time()-self.frame_received_at)*1000),'frameTimestampAvailable':False,'freshness':'unverified',
            'warning':'Veency has no producer timestamp. Cached or changed pixels do not prove action completion. Compare native UI; prefer screenshotr for independent capture.'}

    def close(self): self.socket.close()
    def __enter__(self): return self
    def __exit__(self,*unused): self.close()
