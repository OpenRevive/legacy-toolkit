"""Small ctypes adapter for libimobiledevice's public C API (controller only)."""
from __future__ import annotations
import ctypes as C
import ctypes.util
from pathlib import Path
import plistlib

P = C.c_void_p
S = C.c_char_p
U = C.c_uint32
H = C.c_uint64


def check(code, operation):
    if code != 0:
        raise RuntimeError('%s failed (%d)' % (operation, code))


def library(name):
    found = C.util.find_library(name)
    if found:
        return C.CDLL(found)
    for prefix in ('/opt/homebrew/lib', '/usr/local/lib', '/usr/lib'):
        for suffix in ('.dylib', '.so'):
            path = Path(prefix) / ('lib' + name + suffix)
            if path.is_file(): return C.CDLL(str(path))
    raise RuntimeError('Install libimobiledevice and libplist on the controller: missing ' + name)


class API:
    def __init__(self):
        self.lib = library('imobiledevice-1.0')
        self.plist = library('plist-2.0')
        signatures = {
            'idevice_new': [C.POINTER(P), S], 'idevice_free': [P],
            'lockdownd_client_new_with_handshake': [P, C.POINTER(P), S],
            'lockdownd_client_free': [P],
            'lockdownd_start_service': [P, S, C.POINTER(P)],
            'lockdownd_service_descriptor_free': [P],
            'afc_client_new': [P, P, C.POINTER(P)], 'afc_client_free': [P],
            'afc_file_open': [P, S, C.c_int, C.POINTER(H)],
            'afc_file_close': [P, H],
            'afc_file_read': [P, H, P, U, C.POINTER(U)],
            'afc_file_write': [P, H, P, U, C.POINTER(U)],
            'afc_read_directory': [P, S, C.POINTER(P)],
            'afc_get_file_info': [P, S, C.POINTER(P)],
            'afc_dictionary_free': [P],
            'afc_make_directory': [P, S], 'afc_remove_path': [P, S],
            'afc_rename_path': [P, S, S],
            'mobile_image_mounter_start_service': [P, C.POINTER(P), S],
            'mobile_image_mounter_lookup_image': [P, S, C.POINTER(P)],
            'mobile_image_mounter_mount_image': [P, S, S, U, S, C.POINTER(P)],
            'mobile_image_mounter_free': [P],
            'screenshotr_client_start_service': [P, C.POINTER(P), S],
            'screenshotr_client_free': [P],
        }
        for name, args in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, C.c_int
        self.plist.plist_to_xml.argtypes = [P, C.POINTER(P), C.POINTER(U)]
        self.plist.plist_to_xml.restype = None
        self.plist.plist_mem_free.argtypes = [P]
        self.plist.plist_mem_free.restype = None
        self.plist.plist_free.argtypes = [P]
        self.plist.plist_free.restype = None

    def value(self, node):
        if not node: raise RuntimeError('Device returned no result plist')
        buffer, length = P(), U()
        try:
            self.plist.plist_to_xml(node, C.byref(buffer), C.byref(length))
            return plistlib.loads(C.string_at(buffer, length.value))
        finally:
            if buffer: self.plist.plist_mem_free(buffer)
            self.plist.plist_free(node)


class Device:
    def __init__(self, udid):
        if not udid: raise ValueError('An explicit live UDID is required')
        self.api = API()
        self.lib = self.api.lib
        self.device, self.lockdown = P(), P()
        check(self.lib.idevice_new(C.byref(self.device), udid.encode()), 'connect device')
        try:
            check(self.lib.lockdownd_client_new_with_handshake(self.device, C.byref(self.lockdown), b'OpenRevive'), 'paired lockdown handshake')
        except Exception:
            self.lib.idevice_free(self.device)
            raise

    def service(self, name):
        result = P()
        check(self.lib.lockdownd_start_service(self.lockdown, name.encode(), C.byref(result)), 'start service ' + name)
        return result

    def close(self):
        if self.lockdown: self.lib.lockdownd_client_free(self.lockdown); self.lockdown = P()
        if self.device: self.lib.idevice_free(self.device); self.device = P()

    def __enter__(self): return self
    def __exit__(self, *unused): self.close()


class AFC:
    def __init__(self, device, service='com.apple.afc2'):
        self.device, self.lib, self.client = device, device.lib, P()
        descriptor = device.service(service)
        try:
            check(self.lib.afc_client_new(device.device, descriptor, C.byref(self.client)), 'connect AFC')
        finally:
            self.lib.lockdownd_service_descriptor_free(descriptor)

    def read(self, path, limit=16 * 1024 * 1024):
        handle, result = H(), bytearray()
        check(self.lib.afc_file_open(self.client, path.encode(), 1, C.byref(handle)), 'open ' + path)
        try:
            while True:
                buffer, length = C.create_string_buffer(65536), U()
                code = self.lib.afc_file_read(self.client, handle, buffer, len(buffer), C.byref(length))
                if code == 14 and not length.value: break  # AFC_E_END_OF_DATA
                check(code, 'read ' + path)
                if not length.value: break
                result.extend(buffer.raw[:length.value])
                if len(result) > limit: raise RuntimeError('AFC read exceeds limit: ' + path)
            return bytes(result)
        finally:
            self.lib.afc_file_close(self.client, handle)

    def write(self, path, data):
        handle = H()
        check(self.lib.afc_file_open(self.client, path.encode(), 3, C.byref(handle)), 'open for write ' + path)
        try:
            for offset in range(0, len(data), 65536):
                chunk, count = data[offset:offset + 65536], U()
                buffer = C.create_string_buffer(chunk)
                check(self.lib.afc_file_write(self.client, handle, buffer, len(chunk), C.byref(count)), 'write ' + path)
                if count.value != len(chunk): raise RuntimeError('Short AFC write: ' + path)
        finally:
            self.lib.afc_file_close(self.client, handle)

    def strings(self, function, path):
        result = P()
        check(function(self.client, path.encode(), C.byref(result)), 'AFC metadata ' + path)
        try:
            values, pointer = [], C.cast(result, C.POINTER(S))
            for i in range(100000):
                if pointer[i] is None: return values
                values.append(pointer[i].decode('utf-8', errors='replace'))
            raise RuntimeError('Too many AFC metadata entries')
        finally:
            self.lib.afc_dictionary_free(result)

    def list(self, path): return self.strings(self.lib.afc_read_directory, path)
    def info(self, path):
        values = self.strings(self.lib.afc_get_file_info, path)
        return dict(zip(values[::2], values[1::2]))
    def mkdir(self, path): check(self.lib.afc_make_directory(self.client, path.encode()), 'mkdir ' + path)
    def remove(self, path): check(self.lib.afc_remove_path(self.client, path.encode()), 'remove ' + path)
    def rename(self, source, target): check(self.lib.afc_rename_path(self.client, source.encode(), target.encode()), 'rename AFC file')
    def close(self):
        if self.client: self.lib.afc_client_free(self.client); self.client = P()
    def __enter__(self): return self
    def __exit__(self, *unused): self.close()


if __name__ == '__main__':
    # A shell service can finish its work without completing lockdown's socket
    # check-in. Its caller verifies a unique AFC receipt, then closes this worker.
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--trigger':
        with Device(sys.argv[2]) as device:
            descriptor = device.service('org.openrevive.exec')
            device.lib.lockdownd_service_descriptor_free(descriptor)
    else:
        raise SystemExit('Internal worker; use openrevive device commands')
