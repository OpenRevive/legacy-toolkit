"""Independent lossless PNG pixel oracle for artifact/crop acceptance."""
import struct
import zlib


def pixels(data):
    assert data[:8]==b'\x89PNG\r\n\x1a\n'
    at=8;compressed=b''
    while at<len(data):
        size=struct.unpack('>I',data[at:at+4])[0];kind=data[at+4:at+8];body=data[at+8:at+8+size]
        assert zlib.crc32(kind+body)&0xffffffff==struct.unpack('>I',data[at+8+size:at+12+size])[0]
        if kind==b'IHDR':width,height,depth,color,compression,filtering,interlaced=struct.unpack('>IIBBBBB',body)
        if kind==b'IDAT':compressed+=body
        at+=size+12
    assert depth==8 and color in (2,6) and not interlaced
    channels=3 if color==2 else 4;stride=width*channels
    raw=zlib.decompress(compressed);assert len(raw)==(stride+1)*height
    previous=bytes(stride);rows=[]
    def paeth(a,b,c):
        p=a+b-c;distances=[abs(p-a),abs(p-b),abs(p-c)]
        return (a,b,c)[distances.index(min(distances))]
    for y in range(height):
        kind=raw[y*(stride+1)];row=bytearray(raw[y*(stride+1)+1:(y+1)*(stride+1)])
        assert kind in range(5)
        for i in range(stride):
            left=row[i-channels]if i>=channels else 0
            above=previous[i];corner=previous[i-channels]if i>=channels else 0
            predictor=(0,left,above,(left+above)//2,paeth(left,above,corner))[kind]
            row[i]=(row[i]+predictor)&255
        rows.append(b''.join(row[i:i+3]for i in range(0,stride,channels)))
        previous=row
    return width,height,b''.join(rows)


def cropped(rgb,width,crop):
    x,y,w,h=crop
    return b''.join(rgb[((y+i)*width+x)*3:((y+i)*width+x+w)*3]for i in range(h))


if __name__=='__main__':
    import json,sys
    from pathlib import Path
    full,part,region=sys.argv[1:]
    region=json.loads(region)
    width,height,rgb=pixels(Path(full).read_bytes())
    cw,ch,actual=pixels(Path(part).read_bytes())
    assert (cw,ch)==tuple(region[2:])
    assert actual==cropped(rgb,width,region),'Physical crop differs from the declared full-screen rectangle'
    print('OPENREVIVE_PHYSICAL_NATIVE_CROP_PIXELS_OK')
