#!/usr/bin/env python3
"""Verify built Mach-O/signature bytes, IPA payload and Debian package independently."""
import argparse
import hashlib
import io
import json
import plistlib
from pathlib import Path
import struct
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/'examples/theos-probe'


def signature(data, offset, length):
    blob = data[offset:offset+length]
    magic, size, count = struct.unpack_from('>III', blob)
    assert magic == 0xfade0cc0 and size <= length and count > 0, 'Malformed signature'
    directories = []
    for i in range(count):
        kind, start = struct.unpack_from('>II', blob, 12+i*8)
        if kind != 0: continue
        directory = blob[start:]
        values = struct.unpack_from('>9I4B', directory)
        magic, size, version, flags, hashes, ident, special, slots, limit, width, algorithm, platform, pages = values
        assert magic == 0xfade0c02 and algorithm == 1 and width == 20, 'iOS 6 needs a primary SHA-1 CodeDirectory'
        assert slots > 0 and limit == offset and hashes + slots*width <= size, 'Malformed CodeDirectory'
        for index in range(slots):
            start_byte = index * (1 << pages)
            block = data[start_byte:min(start_byte+(1 << pages), limit)]
            assert hashlib.sha1(block).digest() == directory[hashes+index*width:hashes+(index+1)*width], 'Code signature page mismatch'
        directories.append({'algorithm': 'sha1', 'pages': slots})
    assert directories, 'No primary CodeDirectory'
    return directories


def macho(data):
    magic, cpu, subtype, filetype, count, length, flags = struct.unpack_from('<7I', data)
    assert magic == 0xfeedface and cpu == 12 and subtype == 9 and filetype == 2, 'Expected an ARMv7 executable'
    position = 28; minimum = None; signatures = []; libraries = []
    for _ in range(count):
        cmd, size = struct.unpack_from('<II', data, position)
        assert size >= 8 and position+size <= 28+length
        if cmd == 0x25:
            minimum, sdk = struct.unpack_from('<II', data, position+8)
            assert minimum == 0x60000 and sdk == 0x60100, 'Wrong deployment/SDK metadata'
        if cmd == 0x1d:
            offset, length_sig = struct.unpack_from('<II', data, position+8)
            signatures.extend(signature(data, offset, length_sig))
        assert cmd != 0x80000034, 'Chained fixups are incompatible with iOS 6'
        if cmd == 1:
            assert data[position+8:position+24].rstrip(b'\0') != b'__DATA_CONST', 'DATA_CONST is incompatible with iOS 6'
        if cmd == 0xc:
            start, = struct.unpack_from('<I', data, position+8)
            name = data[position+start:position+size].split(b'\0')[0].decode()
            assert name.startswith(('/System/Library/Frameworks/', '/usr/lib/')), 'Unexpected dependency: ' + name
            libraries.append(name)
        position += size
    assert minimum == 0x60000 and signatures and libraries
    return {'architecture': 'armv7', 'minimum_ios': '6.0', 'sdk': '6.1', 'signature': signatures,
            'libraries': libraries, 'sha256': hashlib.sha256(data).hexdigest()}


def ar(data):
    assert data.startswith(b'!<arch>\n')
    result = {}; position = 8
    while position < len(data):
        header = data[position:position+60]
        assert header[58:60] == b'`\n'
        name = header[:16].decode().strip().rstrip('/')
        size = int(header[48:58]); start = position+60
        result[name] = data[start:start+size]
        position = start+size+(size % 2)
    return result


def verify(ipa, deb):
    app = EXAMPLE/'.theos/_/Applications/ORTheosProbe.app'
    executable = (app/'ORTheosProbe').read_bytes()
    tool = (EXAMPLE/'.theos/_/usr/bin/or-theos-check').read_bytes()
    report = {'application': macho(executable), 'tool': macho(tool)}
    with zipfile.ZipFile(ipa) as archive:
        assert archive.read('Payload/ORTheosProbe.app/ORTheosProbe') == executable
        info = plistlib.loads(archive.read('Payload/ORTheosProbe.app/Info.plist'))
        assert info['CFBundleIdentifier'] == 'org.openrevive.theosprobe' and info['MinimumOSVersion'] == '6.0'
    parts = ar(deb.read_bytes())
    assert parts['debian-binary'] == b'2.0\n'
    with tarfile.open(fileobj=io.BytesIO(parts['data.tar.gz']), mode='r:gz') as archive:
        files = {m.name.lstrip('./'): archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
        assert files['Applications/ORTheosProbe.app/ORTheosProbe'] == executable
        assert files['usr/bin/or-theos-check'] == tool
    report['ipa_sha256'] = hashlib.sha256(ipa.read_bytes()).hexdigest()
    report['deb_sha256'] = hashlib.sha256(deb.read_bytes()).hexdigest()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', type=Path, default=EXAMPLE/'build/ORTheosProbe.ipa')
    parser.add_argument('--deb', type=Path)
    args = parser.parse_args()
    deb = args.deb or max((EXAMPLE/'packages').glob('*.deb'), key=lambda p:p.stat().st_mtime)
    print(json.dumps(verify(args.ipa, deb), indent=2))
    print('THEOS_LEGACY_ARTIFACTS_OK')
