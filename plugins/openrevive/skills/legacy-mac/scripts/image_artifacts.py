"""Pixel-exact PNG crops using controller macOS ImageIO, without extra packages."""
from __future__ import annotations
import ctypes as C
from functools import lru_cache
import os
from pathlib import Path
import sys
import tempfile


class Point(C.Structure): _fields_=[('x',C.c_double),('y',C.c_double)]
class Size(C.Structure): _fields_=[('width',C.c_double),('height',C.c_double)]
class Rect(C.Structure): _fields_=[('origin',Point),('size',Size)]


@lru_cache(maxsize=1)
def api():
    if sys.platform!='darwin':raise RuntimeError('Native PNG cropping requires controller macOS ImageIO; VNC cropping is portable')
    cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cg=C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    io=C.CDLL('/System/Library/Frameworks/ImageIO.framework/ImageIO')
    def bind(lib,name,args,result=C.c_void_p):
        value=getattr(lib,name);value.argtypes=args;value.restype=result;return value
    p=C.c_void_p
    return {
        'url':bind(cf,'CFURLCreateFromFileSystemRepresentation',[p,C.c_char_p,C.c_long,C.c_bool]),
        'string':bind(cf,'CFStringCreateWithCString',[p,C.c_char_p,C.c_uint32]),
        'release':bind(cf,'CFRelease',[p],None),
        'source':bind(io,'CGImageSourceCreateWithURL',[p,p]),
        'image':bind(io,'CGImageSourceCreateImageAtIndex',[p,C.c_size_t,p]),
        'width':bind(cg,'CGImageGetWidth',[p],C.c_size_t),
        'height':bind(cg,'CGImageGetHeight',[p],C.c_size_t),
        'crop':bind(cg,'CGImageCreateWithImageInRect',[p,Rect]),
        'destination':bind(io,'CGImageDestinationCreateWithURL',[p,p,C.c_size_t,p]),
        'add':bind(io,'CGImageDestinationAddImage',[p,p,p],None),
        'finalize':bind(io,'CGImageDestinationFinalize',[p],C.c_bool),
    }


def crop_png(source,output,crop):
    if not isinstance(crop,(tuple,list))or len(crop)!=4 or any(type(v)is not int for v in crop):
        raise ValueError('Crop must be integer [x,y,width,height]')
    x,y,w,h=crop
    if min(x,y)<0 or min(w,h)<1:raise ValueError('Invalid crop bounds')
    functions=api();refs=[]
    def keep(value):
        if not value:raise RuntimeError('ImageIO could not decode/crop/write PNG')
        refs.append(value);return value
    def url(path):
        raw=os.fsencode(Path(path).resolve())
        return keep(functions['url'](None,raw,len(raw),False))
    output=Path(output)
    fd,name=tempfile.mkstemp(prefix='.'+output.name+'-',suffix='.png',dir=output.parent)
    os.close(fd)
    try:
        image=keep(functions['image'](keep(functions['source'](url(source),None)),0,None))
        sw,sh=functions['width'](image),functions['height'](image)
        if x+w>sw or y+h>sh:raise ValueError('Crop outside native screen')
        cropped=keep(functions['crop'](image,Rect(Point(x,y),Size(w,h))))
        if (functions['width'](cropped),functions['height'](cropped))!=(w,h):raise RuntimeError('ImageIO crop dimensions changed')
        png=keep(functions['string'](None,b'public.png',0x08000100))
        destination=keep(functions['destination'](url(name),png,1,None))
        functions['add'](destination,cropped,None)
        if not functions['finalize'](destination):raise RuntimeError('ImageIO PNG finalization failed')
        os.replace(name,output)
        return {'screenWidth':sw,'screenHeight':sh,'crop':list(crop),'imageOrigin':[x,y]}
    finally:
        for value in reversed(refs):functions['release'](value)
        Path(name).unlink(missing_ok=True)
