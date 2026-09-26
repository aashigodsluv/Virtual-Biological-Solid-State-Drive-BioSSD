from __future__ import annotations
import argparse, hashlib, json, logging, os, sys, threading, time, webbrowser
from pathlib import Path, PureWindowsPath

if os.name != 'nt':
    raise SystemExit('UMDI Windows Drive requires Windows 10/11.')

try:
    from winfspy import FileSystem
    from winfspy.memfs import InMemoryFileSystemOperations, FileObj
    from winfspy.plumbing.win32_filetime import filetime_now
except Exception as e:
    raise SystemExit('WinFsp/WinFSPy is required. Run INSTALL_WINDOWS.bat first.\n'+str(e))

import umdi_app

DNA_CAPACITY_BYTES = umdi_app.DNA_CAPACITY_BYTES
CACHE_CAPACITY_BYTES = umdi_app.CACHE_CAPACITY_BYTES
HOST = umdi_app.HOST
DEVICE = umdi_app.DEVICE
SESSION = umdi_app.SESSION
Handler = umdi_app.Handler
ThreadingHTTPServer = umdi_app.ThreadingHTTPServer


def clean_name(path: PureWindowsPath) -> str:
    s = str(path).replace('\\', '/').lstrip('/')
    return s

class UMDIDriveOperations(InMemoryFileSystemOperations):
    """Windows file-system surface backed by the UMDI Host->Protocol->Device path.

    WinFsp handles Windows IRPs. This class turns file close/flush/delete/rename
    events into UMDI transactions. The molecular namespace remains sparse: only
    bytes actually written occupy local backing storage.

    It also tracks open file handles via SESSION (umdi_app.ActivitySession) so
    the dashboard can show the drive as ACTIVE for the entire time a file is
    held open by any application - a document loaded in Word, a video/audio
    file attached to a player, a game reading its assets - not just while a
    UMDI WRITE/READ transaction is mid-flight. A handle is registered the
    moment Windows opens or creates it and released only when that specific
    handle is closed, so multiple simultaneous openers of the same file are
    tracked correctly and the state does not flip to IDLE until the last one
    closes.
    """
    def __init__(self, label='BioSSD', read_only=False):
        super().__init__(label, read_only=read_only)
        self._volume_info['total_size'] = DNA_CAPACITY_BYTES
        self._volume_info['free_size'] = max(0, DNA_CAPACITY_BYTES - DEVICE._used())
        self._dirty = set()
        self._known = set()
        self._open_names = {}  # id(file_context) -> name, for handles this class registered
        self._load_from_umdi()

    def _load_from_umdi(self):
        # Restore files already persisted by UMDI. Directories are reconstructed.
        for name, meta in list(DEVICE.files.items()):
            path = PureWindowsPath('/') / PureWindowsPath(name.replace('/', '\\'))
            cur = PureWindowsPath('/')
            for part in path.parts[1:-1]:
                cur = cur / part
                if cur not in self._entries:
                    self._create_directory(str(cur).lstrip('\\/'))
            try:
                data, _ = HOST.send('READ', name=name)
                # Use the memory FS object only as Windows' open-file cache.
                parent = path.parent
                if parent not in self._entries:
                    continue
                from winfspy.memfs import FileObj
                from winfspy import FILE_ATTRIBUTE
                obj = FileObj(path, FILE_ATTRIBUTE.FILE_ATTRIBUTE_ARCHIVE,
                              self._root_obj.security_descriptor, len(data))
                obj.write(data, 0, False)
                self._entries[path] = obj
                self._known.add(name)
            except Exception:
                logging.exception('Unable to restore UMDI object %s', name)

    def _sync_file(self, file_obj):
        if not isinstance(file_obj, FileObj):
            return
        name = clean_name(file_obj.path)
        data = bytes(file_obj.data[:file_obj.file_size])
        HOST.send('WRITE', binary=data, name=name)
        self._known.add(name)
        self._dirty.discard(file_obj.path)
        self._volume_info['free_size'] = max(0, DNA_CAPACITY_BYTES - DEVICE._used())

    # ---- open-handle tracking: any granted handle on a real file counts as
    # an active session on that name, regardless of read/write activity ----
    def _register_open(self, file_context):
        obj = getattr(file_context, 'file_obj', None)
        if isinstance(obj, FileObj):
            name = clean_name(obj.path)
            self._open_names[id(file_context)] = name
            SESSION.open(name)

    def _release_open(self, file_context):
        name = self._open_names.pop(id(file_context), None)
        if name:
            SESSION.close(name)

    def open(self, *args, **kwargs):
        file_context = super().open(*args, **kwargs)
        self._register_open(file_context)
        return file_context

    def create(self, *args, **kwargs):
        file_context = super().create(*args, **kwargs)
        self._register_open(file_context)
        return file_context

    def read(self, *args, **kwargs):
        result = super().read(*args, **kwargs)
        file_context = args[0] if args else kwargs.get('file_context')
        obj = getattr(file_context, 'file_obj', None)
        if isinstance(obj, FileObj):
            SESSION.touch(clean_name(obj.path))
        return result

    def write(self, file_context, buffer, offset, write_to_end_of_file, constrained_io):
        n = super().write(file_context, buffer, offset, write_to_end_of_file, constrained_io)
        self._dirty.add(file_context.file_obj.path)
        return n

    def overwrite(self, file_context, file_attributes, replace_file_attributes, allocation_size):
        result = super().overwrite(file_context, file_attributes, replace_file_attributes, allocation_size)
        self._dirty.add(file_context.file_obj.path)
        return result

    def set_file_size(self, file_context, new_size, set_allocation_size):
        result = super().set_file_size(file_context, new_size, set_allocation_size)
        if isinstance(file_context.file_obj, FileObj):
            self._dirty.add(file_context.file_obj.path)
        return result

    def flush(self, file_context):
        if isinstance(file_context.file_obj, FileObj) and file_context.file_obj.path in self._dirty:
            self._sync_file(file_context.file_obj)

    def close(self, file_context):
        if isinstance(file_context.file_obj, FileObj) and file_context.file_obj.path in self._dirty:
            self._sync_file(file_context.file_obj)
        self._release_open(file_context)

    def cleanup(self, file_context, file_name, flags):
        obj = file_context.file_obj
        old_name = clean_name(obj.path)
        deleting = bool(flags & 0x01)
        super().cleanup(file_context, file_name, flags)
        # cleanup() fires the moment the app itself closes its handle - this
        # is the real "the player/doc closed" signal. close() also releases
        # as a backup, but Windows can delay close() well after the app is
        # done (cache manager keeps the file open behind the scenes), which
        # is why relying on close() alone left the box stuck on ACTIVE.
        self._release_open(file_context)
        if deleting and old_name in self._known:
            try: HOST.send('DELETE', name=old_name)
            except Exception: logging.exception('UMDI delete failed: %s', old_name)
            self._known.discard(old_name)
            self._dirty.discard(obj.path)
            # A deleted file can no longer be "open" from the dashboard's perspective.
            SESSION.handles.pop(old_name, None)
        elif isinstance(obj, FileObj) and obj.path in self._dirty:
            self._sync_file(obj)
        self._volume_info['free_size'] = max(0, DNA_CAPACITY_BYTES - DEVICE._used())

    def rename(self, file_context, file_name, new_file_name, replace_if_exists):
        old_path = PureWindowsPath(file_name)
        old_name = clean_name(old_path)
        super().rename(file_context, file_name, new_file_name, replace_if_exists)
        obj = file_context.file_obj
        if isinstance(obj, FileObj):
            new_name = clean_name(obj.path)
            self._dirty.add(obj.path)
            self._sync_file(obj)
            if old_name != new_name and old_name in self._known:
                try: HOST.send('DELETE', name=old_name)
                except Exception: pass
                self._known.discard(old_name)
            # Keep the open-handle registration in sync with the new name.
            if old_name != new_name and id(file_context) in self._open_names:
                with SESSION.lock:
                    if old_name in SESSION.handles:
                        SESSION.handles[new_name] = SESSION.handles.pop(old_name)
                self._open_names[id(file_context)] = new_name


def create_fs(mountpoint: str):
    ops = UMDIDriveOperations('BioSSD')
    fs = FileSystem(
        mountpoint, ops,
        sector_size=512,
        sectors_per_allocation_unit=512,  # 256 KiB UMDI logical allocation unit
        volume_creation_time=filetime_now(),
        volume_serial_number=0x554D4449,  # 'UMDI'
        file_info_timeout=1000,
        case_sensitive_search=0,
        case_preserved_names=1,
        unicode_on_disk=1,
        persistent_acls=1,
        post_cleanup_when_modified_only=1,
        um_file_context_is_user_context2=1,
        file_system_name='UMDI-BioSSD',
        debug=False,
    )
    return fs


def start_dashboard(port: int):
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv

def find_available_drive():
    for letter in "BDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{letter}:"
        if not Path(drive + "\\").exists():
            return drive
    raise SystemExit("No available Windows drive letter was found for BioSSD.")

def main():
    p = argparse.ArgumentParser(description='UMDI BioSSD Windows host integration')
    p.add_argument(
    '--drive',
    default=None,
    help='Optional Windows drive letter. If omitted, BioSSD selects an available drive automatically.'
)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--no-browser', action='store_true')
    a = p.parse_args()
    drive = a.drive.upper() if a.drive else find_available_drive()
    if len(drive) != 2 or drive[1] != ':':
        raise SystemExit('Drive must look like D:')
    if Path(drive + '\\').exists():
        raise SystemExit(f'{drive} is already in use. Run with --drive X: for another letter.')

    fs = create_fs(drive)
    dashboard = start_dashboard(a.port)
    try:
        fs.start()
        umdi_app.set_mount_state(True, drive)

        url = f'http://127.0.0.1:{a.port}'
        print('Virtual BioSSD v1.0.0')
        print(f'BioSSD mounted as {drive}')
        print('Molecular capacity: 1 g DNA = 215 PB')
        print('Electronic staging/cache SSD: 1 TB')
        print('UMDI logical block: 256 KiB | effective channels: 4096')
        print('READ profile: 4x / 869 MB/s | WRITE profile: 8x / 894 MB/s')
        print('Control & Diagnostics:', url)
        print('Keep this window open while BioSSD is mounted. Ctrl+C unmounts.')
        if not a.no_browser:
            threading.Timer(.5, lambda: webbrowser.open(url)).start()
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        umdi_app.set_mount_state(False, None)
        dashboard.shutdown()
        fs.stop()

if __name__ == '__main__':
    main()