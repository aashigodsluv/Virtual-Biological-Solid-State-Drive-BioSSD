from __future__ import annotations
import argparse, hashlib, json, math, mimetypes, os, threading, time, uuid, webbrowser
from dataclasses import dataclass, asdict, field
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'runtime'
OBJECTS = STATE / 'objects'
STATE.mkdir(exist_ok=True); OBJECTS.mkdir(exist_ok=True)

# Architecture frozen by the BioSSD/UMDI monograph + simulator.
DNA_CAPACITY_BYTES = 215_000_000_000_000_000
CACHE_CAPACITY_BYTES = 1_000_000_000_000
BLOCK_SIZE = 256 * 1024
CHANNELS = 4096
READ_ACCEL = 4
WRITE_ACCEL = 8
READ_MBPS = 869.0
WRITE_MBPS = 894.0
SSD_CLASS_MIN_MBPS = 500.0

MOUNT_STATE = {'mounted': False, 'drive': None, 'label': 'BioSSD', 'updated': time.time()}
MOUNT_LOCK = threading.RLock()

def set_mount_state(mounted: bool, drive: str | None = None):
    with MOUNT_LOCK:
        MOUNT_STATE.update({'mounted': bool(mounted), 'drive': drive, 'updated': time.time()})

def get_mount_state():
    with MOUNT_LOCK:
        return dict(MOUNT_STATE)


class ActivitySession:
    """Tracks open file handles on the mounted UMDI volume.

    A handle counts as 'active' the moment Windows opens it (a doc loading, a
    video/game/player attaching to the file) and stays active until every
    handle on that name is closed - independent of whether the file is
    currently being read, paused, or just sitting open. This is deliberately
    separate from Tx/transaction bookkeeping, which only covers explicit
    UMDI WRITE/READ/DELETE operations.
    """
    LEAK_BACKSTOP_SECONDS = 300.0  # only for apps that crash/never signal close - not a normal-use timer
    GRACE_SECONDS = 1.5  # a handle must stay open this long to count as real use, not a thumbnail peek

    def __init__(self):
        self.lock = threading.RLock()
        self.handles = {}     # name -> open handle count (real, from open()/close())
        self.last_seen = {}   # name -> timestamp of last open/read activity
        self.open_since = {}  # name -> timestamp the current open streak started (0 -> 1 transition)

    def open(self, name: str):
        with self.lock:
            if self.handles.get(name, 0) == 0:
                self.open_since[name] = time.time()
            self.handles[name] = self.handles.get(name, 0) + 1
            self.last_seen[name] = time.time()

    def touch(self, name: str):
        """Call on every read so last_seen tracks real activity (pause/resume safe)."""
        with self.lock:
            if name in self.handles:
                self.last_seen[name] = time.time()

    def close(self, name: str):
        with self.lock:
            if name in self.handles:
                self.handles[name] -= 1
                if self.handles[name] <= 0:
                    del self.handles[name]
                    self.last_seen.pop(name, None)
                    self.open_since.pop(name, None)

    def snapshot(self):
        with self.lock:
            now = time.time()
            # Defensive only: recovers from a handle an app never released
            # (crash, killed process). Normal pause/resume never hits this -
            # a paused player still holds its handle, so the real close()
            # signal (not a short guess-timer) is what should end a session.
            leaked = [n for n in self.handles if now - self.last_seen.get(n, 0) > self.LEAK_BACKSTOP_SECONDS]
            for n in leaked:
                del self.handles[n]
                self.last_seen.pop(n, None)
                self.open_since.pop(n, None)
            # A handle open for less than GRACE_SECONDS is treated as a
            # background peek (Explorer thumbnail/preview generation), not
            # genuine use - it opens and closes almost instantly, so it never
            # crosses the threshold. Real playback/editing keeps the handle
            # open well past it.
            active_names = sorted(n for n in self.handles if now - self.open_since.get(n, now) >= self.GRACE_SECONDS)
            return {'active': bool(active_names), 'names': active_names, 'handle_count': sum(self.handles.values())}

SESSION = ActivitySession()


@dataclass
class Tx:
    id: str; op: str; name: str; bytes: int; blocks: int; status: str
    started: float; ended: float | None = None; bank: int = 0; channels: int = 0
    message: str = ''; progress: int = 0; errors: int = 0; retries: int = 0
    cache_state: str = 'IDLE'; verification_state: str = 'PENDING'
    ecc_state: str = 'PENDING'; persistence_state: str = 'PENDING'
    events: list = field(default_factory=list)

class Protocol:
    """UMDI Host-Device Protocol: command envelope and validation."""
    OPS = {'IDENTIFY','WRITE','READ','DELETE','VERIFY','GET_STATUS','GET_TELEMETRY','INJECT_FAULT','CLEAR_FAULTS'}
    @staticmethod
    def command(op, **payload):
        if op not in Protocol.OPS: raise ValueError('unsupported UMDI opcode')
        return {'version':1,'id':uuid.uuid4().hex,'opcode':op,'payload':payload,'ts':time.time()}

class VirtualBioSSD:
    """Software-side BioSSD endpoint with sparse logical molecular capacity."""
    def __init__(self):
        self.lock=threading.RLock(); self.faulted=set(); self.active_channels=0
    def identify(self):
        return {'device':'Virtual BioSSD','medium':'1 g DNA','molecular_capacity_bytes':DNA_CAPACITY_BYTES,
                'electronic_cache_bytes':CACHE_CAPACITY_BYTES,'logical_block_bytes':BLOCK_SIZE,
                'effective_channels':CHANNELS,'read_acceleration_x':READ_ACCEL,'write_acceleration_x':WRITE_ACCEL,
                'read_target_MBps':READ_MBPS,'write_target_MBps':WRITE_MBPS,'ssd_class_min_MBps':SSD_CLASS_MIN_MBPS}
    def allocate(self, blocks):
        with self.lock:
            healthy=CHANNELS-len(self.faulted)
            if healthy <= 0: raise IOError('all molecular channels faulted')
            n=max(1,min(blocks,healthy)); self.active_channels += n; return n
    def release(self,n):
        with self.lock: self.active_channels=max(0,self.active_channels-n)
    def target_seconds(self,nbytes, write=False):
        mbps=WRITE_MBPS if write else READ_MBPS
        return nbytes/(mbps*1_000_000) if nbytes else 0
    def write_object(self, oid, data):
        p=OBJECTS/oid; p.write_bytes(data); return hashlib.sha256(data).hexdigest()
    def read_object(self, oid): return (OBJECTS/oid).read_bytes()
    def delete_object(self, oid):
        p=OBJECTS/oid
        if p.exists(): p.unlink()
    def inject_fault(self, channel):
        if not 0 <= channel < CHANNELS: raise ValueError('channel out of range')
        self.faulted.add(channel)
    def clear_faults(self): self.faulted.clear()

class DeviceSoftware:
    """UMDI device-side controller: mapping, scheduling, staging, persistence, verification, telemetry."""
    def __init__(self, hw):
        self.hw=hw; self.files={}; self.transactions=[]; self.next_lba=0; self.bank_cursor=0; self.lock=threading.RLock()
        self._load()
    def _load(self):
        p=STATE/'metadata.json'
        if p.exists():
            try:
                d=json.loads(p.read_text()); self.files=d.get('files',{}); self.next_lba=d.get('next_lba',0)
            except Exception: pass
    def _save(self):
        (STATE/'metadata.json').write_text(json.dumps({'files':self.files,'next_lba':self.next_lba},indent=2))
    def _used(self): return sum(x['size'] for x in self.files.values())
    def _tx(self,op,name,size):
        blocks=(size+BLOCK_SIZE-1)//BLOCK_SIZE if size else 0
        t=Tx(uuid.uuid4().hex[:12],op,name,size,blocks,'QUEUED',time.time())
        self.transactions.insert(0,t); self.transactions=self.transactions[:200]
        self._stage(t,'QUEUED',2)
        return t
    def _stage(self, tx, status, progress=None, **state):
        tx.status=status
        if progress is not None: tx.progress=progress
        for k,v in state.items(): setattr(tx,k,v)
        tx.events.append({'stage':status,'ts':time.time(),'progress':tx.progress})
    def execute(self, cmd, binary=None):
        op=cmd['opcode']; p=cmd['payload']
        if op=='IDENTIFY': return self.hw.identify()
        if op in ('GET_STATUS','GET_TELEMETRY'): return self.status()
        if op=='CLEAR_FAULTS': self.hw.clear_faults(); return self.status()
        if op=='INJECT_FAULT': self.hw.inject_fault(int(p['channel'])); return self.status()
        if op=='WRITE': return self.write(p['name'], binary or b'')
        if op=='READ': return self.read(p['name'])
        if op=='DELETE': return self.delete(p['name'])
        if op=='VERIFY': return self.verify(p['name'])
        raise ValueError('unsupported command')
    def write(self,name,data):
        with self.lock:
            if self._used()-self.files.get(name,{}).get('size',0)+len(data)>DNA_CAPACITY_BYTES: raise IOError('molecular capacity exceeded')
            tx=self._tx('WRITE',name,len(data))
            self._stage(tx,'LOGICAL_BLOCK_MAPPING',12)
            self._stage(tx,'ELECTRONIC_STAGED',25,cache_state='STAGED')
            blocks=max(1,tx.blocks); n=self.hw.allocate(blocks); tx.channels=n; tx.bank=self.bank_cursor%64; self.bank_cursor+=1
            self._stage(tx,'BANK_SELECTED',38)
            self._stage(tx,'CHANNELS_ALLOCATED',48)
            try:
                self._stage(tx,'MOLECULAR_WRITE',62)
                oid=uuid.uuid4().hex; digest=self.hw.write_object(oid,data)
                self._stage(tx,'VERIFYING',78,verification_state='RUNNING',ecc_state='INTEGRITY_CHECK')
                verified=(hashlib.sha256(self.hw.read_object(oid)).hexdigest()==digest)
                if not verified: raise IOError('verification failed')
                tx.verification_state='PASS'; tx.ecc_state='PASS'
                old=self.files.get(name)
                if old: self.hw.delete_object(old['object_id'])
                lba=self.next_lba; self.next_lba += blocks
                self.files[name]={'name':name,'size':len(data),'sha256':digest,'object_id':oid,'lba_start':lba,'blocks':tx.blocks,
                                  'bank':tx.bank,'persistence':'MOLECULAR_PERSISTENT','updated':time.time()}
                self._save()
                self._stage(tx,'MOLECULAR_PERSISTENT',94,persistence_state='MOLECULAR_PERSISTENT')
                self._stage(tx,'COMPLETE',100); tx.message='WRITE verified and molecular-persistent'; tx.ended=time.time()
                return {'transaction':asdict(tx),'file':self.files[name],'target_service_seconds':self.hw.target_seconds(len(data),True)}
            except Exception as e:
                tx.errors += 1; self._stage(tx,'FAILED',tx.progress); tx.message=str(e); tx.ended=time.time(); raise
            finally: self.hw.release(n)
    def read(self,name):
        with self.lock:
            if name not in self.files: raise FileNotFoundError(name)
            f=self.files[name]; tx=self._tx('READ',name,f['size']); n=self.hw.allocate(max(1,f['blocks'])); tx.channels=n; tx.bank=f['bank']
            self._stage(tx,'MOLECULAR_STORAGE',10); self._stage(tx,'MOLECULAR_READ',28)
            try:
                data=self.hw.read_object(f['object_id']); self._stage(tx,'SENSING_ACQUISITION',46)
                self._stage(tx,'DECODING_ECC',64,ecc_state='INTEGRITY_CHECK')
                if hashlib.sha256(data).hexdigest()!=f['sha256']: raise IOError('integrity verification failed')
                tx.ecc_state='PASS'; tx.verification_state='PASS'
                self._stage(tx,'ELECTRONIC_STAGED',80,cache_state='STAGED')
                self._stage(tx,'HOST_READ',94)
                self._stage(tx,'COMPLETE',100,persistence_state='MOLECULAR_PERSISTENT')
                tx.message='READ reconstructed; SHA-256 PASS'; tx.ended=time.time()
                return data, {'transaction':asdict(tx),'file':f,'target_service_seconds':self.hw.target_seconds(len(data),False)}
            except Exception as e:
                tx.errors += 1; self._stage(tx,'FAILED',tx.progress); tx.message=str(e); tx.ended=time.time(); raise
            finally: self.hw.release(n)
    def verify(self,name):
        data,meta=self.read(name); return {'ok':hashlib.sha256(data).hexdigest()==self.files[name]['sha256'],'sha256':self.files[name]['sha256'],'read':meta}
    def delete(self,name):
        with self.lock:
            f=self.files.pop(name); self.hw.delete_object(f['object_id']); self._save()
            tx=self._tx('DELETE',name,f['size']); self._stage(tx,'COMPLETE',100); tx.message='Object removed from molecular namespace'; tx.ended=time.time()
            return {'deleted':name,'transaction':asdict(tx)}
    def status(self):
        used=self._used(); cache_staged=0
        return {'profile':self.hw.identify(),'molecular_used_bytes':used,'molecular_free_bytes':DNA_CAPACITY_BYTES-used,
                'cache_used_bytes':cache_staged,'cache_free_bytes':CACHE_CAPACITY_BYTES-cache_staged,
                'files':sorted(self.files.values(),key=lambda x:x['updated'],reverse=True),
                'transactions':[asdict(x) for x in self.transactions], 'faulted_channels':sorted(self.hw.faulted),
                'healthy_channels':CHANNELS-len(self.hw.faulted),'active_channels':self.hw.active_channels,
                'mount':get_mount_state(),'session':SESSION.snapshot()}

class HostSoftware:
    """UMDI host-side client. All host operations cross the protocol boundary."""
    def __init__(self,device): self.device=device
    def send(self,op,binary=None,**payload): return self.device.execute(Protocol.command(op,**payload),binary)

HW=VirtualBioSSD(); DEVICE=DeviceSoftware(HW); HOST=HostSoftware(DEVICE)

INDEX = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UMDI Control & Diagnostics</title>
<style>
:root{--bg:#02070b;--cyan:#13d9ff;--cyan2:#008fc8;--green:#55ff72;--amber:#ffe45c;--red:#ff6474;--text:#d7f8ff;--muted:#6d96a5;--line:#0d7897;--panel:rgba(2,11,17,.92);font-family:"Cascadia Mono","Consolas","Courier New",monospace;color:var(--text);background:var(--bg)}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#010509;color:var(--text)}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 78% 18%,rgba(0,184,235,.075),transparent 27%),linear-gradient(rgba(0,170,220,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(0,170,220,.025) 1px,transparent 1px);background-size:auto,32px 32px,32px 32px}
.app{height:100vh;padding:13px 18px 8px;display:grid;grid-template-rows:222px 145px minmax(0,1fr) 28px;gap:9px;position:relative;z-index:1}.frame{border:1px solid rgba(13,120,151,.8);background:linear-gradient(180deg,rgba(2,15,22,.96),rgba(1,8,13,.96));box-shadow:0 0 20px rgba(0,157,205,.045),inset 0 0 24px rgba(0,141,183,.025);position:relative;min-width:0;overflow:hidden}.frame:before,.frame:after{content:"";position:absolute;width:15px;height:15px;pointer-events:none}.frame:before{left:-1px;top:-1px;border-left:2px solid var(--cyan);border-top:2px solid var(--cyan)}.frame:after{right:-1px;bottom:-1px;border-right:2px solid var(--cyan);border-bottom:2px solid var(--cyan)}
.top{display:grid;grid-template-columns:1.45fr .8fr;gap:9px;min-width:0}.console{padding:14px 17px;display:grid;grid-template-columns:minmax(0,1fr) 275px;gap:14px}.title{font-size:23px;color:var(--cyan);text-shadow:0 0 12px rgba(19,217,255,.32);letter-spacing:.025em;white-space:nowrap}.title .pipe{color:#7beaff}.tag{margin:4px 0 13px;color:#73b8ca;font-size:11px;letter-spacing:.06em}.statusline{font-size:11px;line-height:1.55;color:#a8dce7}.ok{color:var(--green)}.info{color:var(--cyan)}.dim{color:var(--muted)}.statusline b{font-weight:500;color:#e7fbff}

/* ---- connection indicator: pulsing green when live ---- */
.conn{display:flex;align-items:center;gap:9px;margin-top:12px;font-size:11px;color:#baf7ff;letter-spacing:.04em}
.dot{position:relative;width:9px;height:9px;border-radius:50%;background:#50636a;flex:0 0 auto}
.dot.on{background:var(--green);box-shadow:0 0 10px var(--green),0 0 20px rgba(85,255,114,.45);animation:dotpulse 1.4s ease-in-out infinite}
.dot.on:after{content:"";position:absolute;inset:-1px;border-radius:50%;border:1px solid var(--green);animation:ring 1.8s ease-out infinite}
@keyframes dotpulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.55;transform:scale(.82)}}
@keyframes ring{0%{transform:scale(1);opacity:.75}100%{transform:scale(3.2);opacity:0}}
.conn b{font-weight:600;color:var(--green);text-shadow:0 0 9px rgba(85,255,114,.4)}

.metrics{border-left:1px solid rgba(13,120,151,.4);padding-left:14px;display:grid;grid-template-rows:auto repeat(5,1fr);align-items:center}.metrichead{font-size:11px;color:var(--cyan);letter-spacing:.12em;border-bottom:1px solid rgba(13,120,151,.35);padding-bottom:6px}.mrow{display:grid;grid-template-columns:1fr auto;gap:10px;font-size:10px;align-items:center;border-bottom:1px dotted rgba(80,142,160,.18)}.mrow:last-child{border:0}.mrow span{color:#8ab0bb}.mrow b{color:#e9fcff;font-size:11px;font-weight:600}.mrow b.green{color:var(--green)}

/* ================= VIRTUAL BIOSSD — 3D GLASS ENCLOSURE ================= */
.device{padding:10px 13px 9px;display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:6px}
.devicehead{display:flex;justify-content:center;align-items:center;color:var(--cyan);font-size:12px;letter-spacing:.14em;text-shadow:0 0 10px rgba(19,217,255,.35)}
.biossd{position:relative;min-height:0;display:flex;align-items:center;justify-content:center;
  background:radial-gradient(ellipse at 50% 58%,rgba(0,70,120,.22) 0%,rgba(0,4,10,.55) 65%);
  border:1px solid rgba(19,217,255,.15);border-radius:5px;overflow:hidden;
  perspective:950px;perspective-origin:50% 45%}
.stage-glow{position:absolute;bottom:10px;left:50%;width:290px;height:46px;transform:translateX(-50%);
  background:radial-gradient(ellipse at center,rgba(0,200,255,.30) 0%,rgba(0,120,255,.10) 45%,transparent 72%);
  filter:blur(9px);pointer-events:none;transition:opacity .4s}
.box-float{transform-style:preserve-3d;animation:float 9s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(-4px)}50%{transform:translateY(4px)}}
.glass-box{position:relative;width:296px;height:108px;transform-style:preserve-3d;
  transform:rotateX(var(--rx,8deg)) rotateY(var(--ry,-16deg));
  transition:transform .32s cubic-bezier(.22,.61,.36,1)}
.face{position:absolute;top:50%;left:50%}
.face-front,.face-back,.face-core{width:296px;height:108px;margin:-54px 0 0 -148px;border-radius:7px}
.face-front{transform:translateZ(19px);pointer-events:none;overflow:hidden;
  background:linear-gradient(135deg,rgba(255,255,255,.22) 0%,rgba(255,255,255,.05) 18%,rgba(255,255,255,0) 40%,rgba(19,217,255,.05) 66%,rgba(255,255,255,.12) 97%),rgba(0,40,70,.20);
  border:1px solid rgba(190,245,255,.55);
  box-shadow:inset 0 1px 1px rgba(255,255,255,.55),inset 0 -1px 1px rgba(255,255,255,.22),inset 0 0 34px rgba(0,200,255,.15),0 0 20px rgba(0,170,255,.32);
  transition:box-shadow .4s}
.face-front:after{content:"";position:absolute;top:-60%;left:-45%;width:28%;height:220%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.30),transparent);
  transform:rotate(18deg);filter:blur(4px);animation:gloss 6.5s ease-in-out infinite}
@keyframes gloss{0%{left:-45%;opacity:0}12%{opacity:1}55%{left:118%;opacity:0}100%{left:118%;opacity:0}}
.face-back{transform:translateZ(-19px);background:linear-gradient(200deg,rgba(0,60,110,.55),rgba(0,10,25,.85));
  border:1px solid rgba(0,190,255,.32);box-shadow:inset 0 0 45px rgba(0,140,255,.26)}
.face-left,.face-right{width:38px;height:108px;margin:-54px 0 0 -19px;
  background:linear-gradient(90deg,rgba(150,240,255,.42),rgba(0,150,220,.20),rgba(150,240,255,.42));
  border-top:1px solid rgba(200,250,255,.5);border-bottom:1px solid rgba(200,250,255,.5);
  box-shadow:inset 0 0 16px rgba(0,220,255,.35)}
.face-left{transform:translateX(-148px) rotateY(90deg)}
.face-right{transform:translateX(148px) rotateY(90deg)}
.face-top,.face-bottom{width:296px;height:38px;margin:-19px 0 0 -148px;
  background:linear-gradient(180deg,rgba(190,250,255,.50),rgba(0,140,210,.20),rgba(190,250,255,.38));
  border-left:1px solid rgba(200,250,255,.45);border-right:1px solid rgba(200,250,255,.45);
  box-shadow:inset 0 0 16px rgba(0,220,255,.38)}
.face-top{transform:translateY(-54px) rotateX(90deg)}
.face-bottom{transform:translateY(54px) rotateX(90deg)}
.face-core{transform:translateZ(0);overflow:hidden;background:radial-gradient(circle at center,#021226 0%,#00050d 100%);box-shadow:0 0 28px rgba(0,170,255,.28)}
#dnaCanvas,#lightCanvas{position:absolute;top:0;left:0;width:100%;height:100%;display:block}
#dnaCanvas{opacity:.9}
#lightCanvas{mix-blend-mode:screen;pointer-events:none}
.reflection{position:absolute;top:50%;left:50%;width:296px;height:108px;margin:-54px 0 0 -148px;
  transform:translateY(116px) rotateX(-180deg) translateZ(1px);
  background:linear-gradient(180deg,rgba(0,190,255,.28),rgba(0,120,220,.05) 45%,transparent 70%);
  filter:blur(8px);opacity:.5;border-radius:7px;pointer-events:none}
.biossd.active .face-front{box-shadow:inset 0 1px 1px rgba(255,255,255,.65),inset 0 -1px 1px rgba(255,255,255,.28),inset 0 0 44px rgba(0,235,255,.26),0 0 34px rgba(0,210,255,.55)}
.biossd.active .stage-glow{opacity:1;filter:blur(11px)}
.biossd.fault .face-front{border-color:rgba(255,120,130,.6);box-shadow:inset 0 0 38px rgba(255,90,110,.25),0 0 24px rgba(255,90,110,.4)}
.devfoot{display:flex;align-items:center;justify-content:center;gap:7px;font-size:10px;letter-spacing:.16em;color:#6c9aa7;padding-top:2px}
.devfoot b{font-weight:700;font-size:11px;color:var(--green);text-shadow:0 0 10px rgba(85,255,114,.5)}
.devfoot b.live{animation:statepulse 1.1s ease-in-out infinite}
@keyframes statepulse{0%,100%{opacity:1}50%{opacity:.45}}

.pipeline{padding:10px 14px}.sectionhead{height:24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(13,120,151,.35);margin-bottom:10px}.sectionhead h2{font-size:11px;font-weight:500;color:var(--cyan);letter-spacing:.11em;margin:0}.sectionhead .right{font-size:8px;color:#6c98a5;max-width:55%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.flow{height:88px;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));grid-template-rows:36px 36px;gap:10px 18px;position:relative}.stage{border:1px solid rgba(13,120,151,.55);display:flex;align-items:center;justify-content:center;text-align:center;padding:4px 8px;font-size:8px;color:#6e9aa7;position:relative;background:rgba(0,26,36,.28);min-width:0}.stage:after{content:"›";position:absolute;right:-13px;color:#146b83;font-size:14px}.stage:nth-child(5):after,.stage:last-child:after{display:none}.stage.done{color:#9deab4;border-color:rgba(85,255,114,.42);background:rgba(20,85,49,.12)}.stage.live{color:#fff;border-color:var(--cyan);box-shadow:0 0 12px rgba(19,217,255,.25),inset 0 0 12px rgba(19,217,255,.08)}.stage.live:before{content:"";position:absolute;left:7px;right:7px;bottom:-1px;height:1px;background:var(--cyan);box-shadow:0 0 8px var(--cyan);animation:stagesweep 1s infinite}@keyframes stagesweep{0%{transform:scaleX(.05);transform-origin:left}100%{transform:scaleX(1);transform-origin:left}}

#progress{font-size:14px;font-weight:700;letter-spacing:.04em;color:#8ab0bb;max-width:none}
#progress.live{color:var(--cyan);text-shadow:0 0 12px rgba(19,217,255,.5);animation:statepulse 1s ease-in-out infinite}
#progress.complete{color:var(--green);text-shadow:0 0 14px rgba(85,255,114,.65)}
#progress.bad{color:var(--red);text-shadow:0 0 12px rgba(255,100,116,.5)}

.lower{display:grid;grid-template-columns:1.35fr .65fr;gap:9px;min-height:0}
.telemetry,.recentbox{padding:10px 14px;min-height:0}
.telemetry{display:grid;grid-template-rows:auto auto minmax(0,1fr);gap:0}
.telebody{height:auto;display:grid;grid-template-columns:1fr 1fr;gap:8px 18px;align-content:start}
.kv{display:grid;grid-template-columns:145px minmax(0,1fr);font-size:9px;line-height:1.55;border-bottom:1px dotted rgba(70,130,145,.17);padding:2px 0;min-width:0}.kv span{color:#7299a5}.kv b{color:#dffaff;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.kv b.green{color:var(--green)}

/* ============ IDLE CHANNEL-HEALTH MONITOR ============ */
.chanmon{display:grid;grid-template-columns:58px minmax(0,1fr) 112px;grid-template-rows:minmax(0,1fr) auto;
  gap:7px 10px;min-height:0;padding:12px 0 2px}
.chanmon.hidden{display:none}
.cm-labels,.cm-status{display:flex;flex-direction:column;justify-content:space-between;min-height:0;font-size:7px;letter-spacing:.08em;line-height:1}
.cm-labels{color:#3f8296;text-align:right;padding:1px 0}
.cm-status{padding:1px 0}
.cm-status div{display:flex;justify-content:space-between;gap:6px;color:var(--cyan);opacity:.85}
.cm-status b{font-weight:600;color:var(--green);opacity:.9}
.cm-gridwrap{position:relative;min-width:0;min-height:0;overflow:hidden;padding:2px;
  border:1px solid rgba(19,217,255,.1);background:rgba(0,18,28,.3);border-radius:3px}
.cm-grid{display:grid;grid-template-columns:repeat(64,1fr);grid-template-rows:repeat(16,1fr);gap:3px;width:100%;height:100%}
.cm-cell{background:rgba(19,217,255,.13);border-radius:1px}
.cm-cell.hot{background:var(--green);box-shadow:0 0 5px rgba(85,255,114,.75);animation:cmpulse 1.3s ease-in-out}
@keyframes cmpulse{0%{opacity:.25}35%{opacity:1}100%{opacity:.5}}
.cm-scan{position:absolute;top:0;bottom:0;width:2px;pointer-events:none;opacity:.7;
  background:linear-gradient(180deg,transparent,var(--cyan),transparent);
  box-shadow:0 0 12px rgba(19,217,255,.85);animation:cmscan 11s linear infinite}
@keyframes cmscan{0%{left:-2px}100%{left:100%}}
.cm-msg{grid-column:1/-1;display:flex;align-items:center;gap:5px;padding-left:2px;
  font-size:8px;letter-spacing:.1em;color:#6fc6dc;transition:opacity .45s ease}
.cm-msg.fade{opacity:0}
.cm-msg .cur{display:inline-block;width:5px;height:8px;background:var(--cyan);box-shadow:0 0 6px rgba(19,217,255,.7);animation:blink 1.05s step-end infinite}
@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}

.faultbar{grid-column:1/-1;display:flex;gap:7px;margin-top:3px}.faultbar input{width:125px;background:#01080c;border:1px solid #14556a;color:#c9f8ff;padding:5px 7px;font:9px inherit}.btn{background:#041820;border:1px solid #16708b;color:#aeefff;padding:5px 9px;font:9px inherit;cursor:pointer}.btn:hover{border-color:var(--cyan);color:white;box-shadow:0 0 10px rgba(19,217,255,.15)}.btn.danger{border-color:#74333d;color:#ffabb4}.recent{height:calc(100% - 35px);overflow:hidden}.tx{display:grid;grid-template-columns:45px minmax(0,1fr) 65px;gap:8px;padding:7px 2px;border-bottom:1px dotted rgba(70,130,145,.2);font-size:8px;min-width:0}.tx .name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#a9cbd3}.tx .op{color:var(--cyan);font-weight:600}.tx .done{color:var(--green);text-align:right}.tx .bad{color:var(--red);text-align:right}.empty{color:#567d89;font-size:9px;padding-top:8px}
.footer{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;font-size:8px;color:#547b87;padding:0 4px}.footer .center{text-align:center}.footer .right{text-align:right}.footer a{color:#6e9ba7;text-decoration:none}.devfab{position:fixed;right:20px;bottom:34px;z-index:20;background:#03151c;border:1px solid #14708b;color:#8ddff0;padding:6px 10px;font:8px inherit;cursor:pointer}.devdrawer{display:none;position:fixed;right:20px;bottom:66px;width:400px;z-index:30;padding:13px;background:rgba(1,9,14,.99);border:1px solid var(--cyan);box-shadow:0 0 35px rgba(0,170,220,.16)}.devdrawer.open{display:block}.devdrawer h3{font-size:10px;color:var(--cyan);margin:0 0 5px}.devdrawer p,.devmsg{font-size:8px;color:#6f9ba7}.devdrop{border:1px dashed #17637a;padding:10px;margin-top:8px}.devdrop input{width:100%;color:#a9dce7;font:8px inherit}.devrow{display:flex;gap:6px;margin-top:7px}.devrow input{flex:1;min-width:0;background:#01080c;border:1px solid #14556a;color:white;padding:5px;font:8px inherit}
@media(max-width:1050px){html,body{overflow:auto}.app{height:auto;min-height:100vh;grid-template-rows:auto auto auto auto}.top,.lower{grid-template-columns:1fr}.console{grid-template-columns:1fr}.metrics{display:none}.device{min-height:210px}.flow{height:auto;grid-template-columns:repeat(3,1fr);grid-template-rows:auto}.stage:after{display:none}.telebody{grid-template-columns:1fr}.telemetry{min-height:420px}}
@media(prefers-reduced-motion:reduce){.box-float{animation:none}.face-front:after{animation:none;opacity:0}.cm-scan{animation:none}}
</style></head><body><main class="app">
<section class="top">
  <div class="frame console"><div><div class="title">BioSSD <span class="pipe">|</span> UMDI Control &amp; Diagnostics</div><div class="tag">UNIVERSAL MOLECULAR DIGITAL INTERFACE • OPERATE • OBSERVE • VERIFY</div><div class="statusline"><span class="info">[INFO]</span> Host Interface ................ <span class="ok">ONLINE</span><br><span class="info">[INFO]</span> Molecular capacity ........... <b>1 g DNA / 215 PB</b><br><span class="info">[INFO]</span> Electronic staging ............ <b>1 TB SSD</b><br><span class="info">[INFO]</span> UMDI logical block ............. <b>256 KiB</b><br><span class="info">[INFO]</span> Effective channels ............. <b>4,096</b></div><div class="conn"><i id="dot" class="dot"></i><span id="conn">CHECKING MOUNT STATE...</span></div></div><div class="metrics"><div class="metrichead">LIVE SYSTEM</div><div class="mrow"><span>Molecular capacity used</span><b id="used">0 B</b></div><div class="mrow"><span>Cache available</span><b id="cache">1.00 TB</b></div><div class="mrow"><span>Channels healthy</span><b id="channels" class="green">4,096</b></div><div class="mrow"><span>READ profile</span><b>4× / 869 MB/s</b></div><div class="mrow"><span>WRITE profile</span><b>8× / 894 MB/s</b></div></div></div>

  <div class="frame device">
    <div class="devicehead">Virtual BioSSD</div>
    <div id="biossd" class="biossd">
      <div class="stage-glow"></div>
      <div class="box-float">
        <div class="glass-box" id="glassBox">
          <div class="face face-back"></div>
          <div class="face face-left"></div>
          <div class="face face-right"></div>
          <div class="face face-top"></div>
          <div class="face face-bottom"></div>
          <div class="face face-core">
            <canvas id="dnaCanvas"></canvas>
            <canvas id="lightCanvas"></canvas>
          </div>
          <div class="face face-front"></div>
          <div class="reflection"></div>
        </div>
      </div>
    </div>
    <div class="devfoot">STATE: <b id="devstate">IDLE</b></div>
  </div>
</section>
<section class="frame pipeline"><div class="sectionhead"><h2>&gt;&gt;&gt; LIVE UMDI TRANSACTION PATH</h2><div id="txheadline" class="right">IDLE • awaiting host I/O</div></div><div id="flow" class="flow"></div></section>
<section class="lower">
  <div class="frame telemetry">
    <div class="sectionhead"><h2>&gt;&gt;&gt; ACTIVE TRANSACTION TELEMETRY</h2><div id="progress" class="right">—</div></div>
    <div id="tele" class="telebody"></div>
    <div id="chanmon" class="chanmon">
      <div class="cm-labels">
        <div>CH 0001</div><div>CH 0512</div><div>CH 1024</div><div>CH 1536</div><div>CH 2048</div>
        <div>CH 2560</div><div>CH 3072</div><div>CH 3584</div><div>CH 4096</div>
      </div>
      <div class="cm-gridwrap"><div id="cmGrid" class="cm-grid"></div><div class="cm-scan"></div></div>
      <div class="cm-status">
        <div><span>BANK A</span><b>READY</b></div>
        <div><span>BANK B</span><b>READY</b></div>
        <div><span>BANK C</span><b>READY</b></div>
        <div><span>BANK D</span><b>READY</b></div>
        <div><span>ECC</span><b>READY</b></div>
        <div><span>CACHE</span><b>READY</b></div>
        <div><span>SENSORS</span><b>ONLINE</b></div>
      </div>
      <div id="cmMsg" class="cm-msg"><span id="cmText">Scanning channels...</span><i class="cur"></i></div>
    </div>
  </div>
  <div class="frame recentbox"><div class="sectionhead"><h2>&gt;&gt;&gt; RECENT TRANSACTIONS</h2><div class="right">REAL UMDI EVENTS</div></div><div id="txs" class="recent"></div></div>
</section>
<footer class="footer"><div id="namespace">Molecular namespace • waiting for telemetry</div><div class="center">© 2026 Abraham Ikongshul Ashindortiang · UMDI / Virtual BioSSD / <a href="mailto:aashigodsluv@gmail.com">aashigodsluv@gmail.com</a></div><div class="right"></div></footer></main>
<button class="devfab" onclick="toggleDev()">[ DEVELOPER TOOLS ]</button>
<aside id="devdrawer" class="devdrawer">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <h3>DEVELOPER / DIAGNOSTIC UI</h3>
    <button class="btn" onclick="toggleDev(false)">CLOSE</button>
  </div>

  <p>Direct UMDI protocol testing. File Explorer remains the normal storage interface.</p>

  <div class="devdrop">
    <input id="devfile" type="file">
    <div class="devrow">
      <input id="devname" type="text" placeholder="Object name (defaults to filename)">
      <button class="btn" onclick="diagWrite()">WRITE TO BIOSSD</button>
    </div>
    <div id="devmsg" class="devmsg">Choose a file to issue a direct diagnostic WRITE.</div>
  </div>

  <div class="devdrop">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:9px">
      <div style="font-size:10px;color:var(--cyan);letter-spacing:.10em;font-weight:600">STORED OBJECTS</div>
      <button class="btn" style="padding:3px 7px;font-size:7px" onclick="refresh()">REFRESH</button>
    </div>
    <div id="devobjects" class="devmsg">Loading stored objects...</div>
  </div>

  <div class="faultbar" style="margin-top:9px">
    <input id="ch" type="number" min="0" max="4095" placeholder="Channel 0–4095">
    <button class="btn danger" onclick="fault()">INJECT FAULT</button>
    <button class="btn" onclick="clearFaults()">CLEAR FAULTS</button>
  </div>
</aside>
<script>
/* ============ 3D GLASS BOX — MOLECULAR CORE ANIMATION ============ */
const CW=296,CH=108;
const dnaC=document.getElementById('dnaCanvas'),dctx=dnaC.getContext('2d');
const litC=document.getElementById('lightCanvas'),lctx=litC.getContext('2d');
const DPR=Math.min(window.devicePixelRatio||1,2);
[dnaC,litC].forEach(c=>{c.width=CW*DPR;c.height=CH*DPR});
dctx.scale(DPR,DPR);lctx.scale(DPR,DPR);

let boost=1,tAcc=0;
function setBoost(on){boost=on?2.6:1}
function node(x,y,dp,c){
  const r=1.1+(dp+1)*1.0;
  dctx.save();dctx.globalAlpha=Math.min(.30+(dp+1)*.33,1);
  dctx.shadowBlur=6*(dp+1);dctx.shadowColor=c;dctx.fillStyle=c;
  dctx.beginPath();dctx.arc(x,y,r,0,Math.PI*2);dctx.fill();dctx.restore();
}
function drawHelix(){
  dctx.fillStyle='#010a17';dctx.fillRect(0,0,CW,CH);
  const pts=34,sp=CW/pts,mid=CH/2,amp=CH*0.33;
  for(let i=0;i<pts;i++){
    const x=i*sp+sp/2,ph=tAcc+i*0.26;
    const y1=mid+Math.sin(ph)*amp,y2=mid+Math.sin(ph+Math.PI)*amp;
    const d1=Math.cos(ph),d2=Math.cos(ph+Math.PI);
    dctx.strokeStyle='rgba(0,180,255,'+(0.10+0.18*(1-Math.abs(d1))).toFixed(3)+')';
    dctx.lineWidth=1;dctx.beginPath();dctx.moveTo(x,y1);dctx.lineTo(x,y2);dctx.stroke();
    node(x,y1,d1,'#00f0ff');node(x,y2,d2,'#0077ff');
  }
}
const COLORS=['#ff0055','#00ffff','#ffee00','#00ff66','#ff00ff','#00aaff'];
const parts=[];
for(let i=0;i<26;i++)parts.push({x:Math.random()*CW,y:Math.random()*CH,
  vx:(Math.random()-.5)*5,vy:(Math.random()-.5)*2.6,
  r:Math.random()*2.2+1.2,c:COLORS[Math.floor(Math.random()*COLORS.length)],a:Math.random()});
function frame(){
  tAcc+=0.030*boost;
  lctx.clearRect(0,0,CW,CH);
  drawHelix();
  parts.forEach(p=>{
    p.x+=p.vx*boost;p.y+=p.vy*boost;
    if(p.x<=0||p.x>=CW)p.vx*=-1;
    if(p.y<=0||p.y>=CH)p.vy*=-1;
    p.a=.3+Math.abs(Math.sin(tAcc*3+p.x));
    lctx.save();lctx.globalAlpha=Math.min(p.a,1);
    lctx.shadowBlur=10;lctx.shadowColor=p.c;lctx.fillStyle=p.c;
    lctx.beginPath();lctx.arc(p.x,p.y,p.r,0,Math.PI*2);lctx.fill();
    lctx.strokeStyle=p.c;lctx.lineWidth=p.r*.8;
    lctx.beginPath();lctx.moveTo(p.x,p.y);lctx.lineTo(p.x-p.vx*2*boost,p.y-p.vy*2*boost);lctx.stroke();
    lctx.restore();
  });
  requestAnimationFrame(frame);
}
frame();
(function(){const st=document.querySelector('#biossd'),bx=document.querySelector('#glassBox');
 st.addEventListener('pointermove',e=>{const r=st.getBoundingClientRect();
   const nx=(e.clientX-r.left)/r.width-.5,ny=(e.clientY-r.top)/r.height-.5;
   bx.style.setProperty('--ry',(-16+nx*26)+'deg');bx.style.setProperty('--rx',(8-ny*15)+'deg')});
 st.addEventListener('pointerleave',()=>{bx.style.setProperty('--ry','-16deg');bx.style.setProperty('--rx','8deg')});
})();

/* ============ IDLE CHANNEL-HEALTH MONITOR ============ */
const CM_CELLS=1024;                       /* 64 x 16 cells, 4 channels per cell */
const cmGridEl=document.getElementById('cmGrid'),cmPanel=document.getElementById('chanmon');
const cmCells=[];
(function(){const f=document.createDocumentFragment();
 for(let i=0;i<CM_CELLS;i++){const d=document.createElement('i');d.className='cm-cell';f.appendChild(d);cmCells.push(d)}
 cmGridEl.appendChild(f)})();
setInterval(()=>{
  if(cmPanel.classList.contains('hidden'))return;
  const n=4+Math.floor(Math.random()*4);
  for(let k=0;k<n;k++){
    const c=cmCells[Math.floor(Math.random()*cmCells.length)];
    if(c.classList.contains('hot'))continue;
    c.classList.add('hot');
    setTimeout(()=>c.classList.remove('hot'),1100+Math.random()*900);
  }
},430);
const CM_MSGS=['Scanning channels...','Channel monitor active','Awaiting host transaction...'];
let cmIdx=0;
setInterval(()=>{
  if(cmPanel.classList.contains('hidden'))return;
  const box=document.getElementById('cmMsg');
  box.classList.add('fade');
  setTimeout(()=>{cmIdx=(cmIdx+1)%CM_MSGS.length;
    document.getElementById('cmText').textContent=CM_MSGS[cmIdx];
    box.classList.remove('fade')},500);
},4300);

/* ==================== UMDI DASHBOARD LOGIC ==================== */
const W=['HOST WRITE','LOGICAL BLOCK MAPPING','ELECTRONIC SSD STAGING','BANK SELECTION','CHANNEL ALLOCATION','MOLECULAR WRITE','VERIFY / ECC','MOLECULAR PERSISTENCE','COMPLETE'];
const R=['MOLECULAR STORAGE','MOLECULAR READ','SENSING / ACQUISITION','DECODING + ECC','ELECTRONIC STAGING','HOST READ','COMPLETE'];
const mapW={QUEUED:0,LOGICAL_BLOCK_MAPPING:1,ELECTRONIC_STAGED:2,BANK_SELECTED:3,CHANNELS_ALLOCATED:4,MOLECULAR_WRITE:5,VERIFYING:6,MOLECULAR_PERSISTENT:7,COMPLETE:8,FAILED:8};
const mapR={QUEUED:0,MOLECULAR_STORAGE:0,MOLECULAR_READ:1,SENSING_ACQUISITION:2,DECODING_ECC:3,ELECTRONIC_STAGED:4,HOST_READ:5,COMPLETE:6,FAILED:6};
const STEP_MS=220,HOLD_MS=1500;let lastId='',replayTimers=[],idleTimer=null,isActive=false;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>{n=Number(n||0);for(const u of ['B','KB','MB','GB','TB','PB']){if(Math.abs(n)<1000)return n.toFixed(n<10?2:1)+' '+u;n/=1000}return n.toFixed(2)+' EB'};
async function api(p,o){let r=await fetch(p,o);if(!r.ok)throw Error(await r.text());return r.json()}
function renderStages(op,idx=-1){let a=op==='READ'?R:W;document.querySelector('#flow').innerHTML=a.map((x,i)=>`<div class="stage ${i<idx?'done':i===idx?'live':''}">${x}</div>`).join('')}

/* ---- device box + STATE label: OR of (transaction mid-flight) and (a file held open on the volume) ---- */
let txBusy=false, sessionBusy=false, sessionNames=[], faultCountGlobal=0;
function applyDeviceVisual(){
  const busy=txBusy||sessionBusy;
  const d=document.querySelector('#biossd'),el=document.querySelector('#devstate');
  d.classList.toggle('active',busy);
  d.classList.toggle('fault',!!faultCountGlobal);
  el.textContent=busy?'ACTIVE':'IDLE';
  el.classList.toggle('live',busy);
  setBoost(busy);
}
function deviceState(op,stage,faultCount){
  txBusy=!!(stage&&stage!=='COMPLETE'&&stage!=='FAILED');
  faultCountGlobal=faultCount;
  applyDeviceVisual();
}
function goIdle(faultCount){isActive=false;renderStages('WRITE',-1);document.querySelector('#txheadline').textContent=sessionBusy?fileOpenHeadline():'IDLE • awaiting host I/O';deviceState('','',faultCount||0)}
function replay(tx,faultCount){
  replayTimers.forEach(clearTimeout);replayTimers=[];
  let mp=tx.op==='READ'?mapR:mapW;
  if(!tx.events?.length){renderStages(tx.op,mp[tx.status]??-1);deviceState(tx.op,tx.status,faultCount);return}
  tx.events.forEach((e,i)=>replayTimers.push(setTimeout(()=>{renderStages(tx.op,mp[e.stage]??-1);deviceState(tx.op,e.stage,faultCount)},i*STEP_MS)));
  replayTimers.push(setTimeout(()=>{renderStages(tx.op,mp[tx.status]??-1);deviceState(tx.op,tx.status,faultCount)},tx.events.length*STEP_MS+100));
}
function fileOpenHeadline(){
  if(!sessionNames.length)return 'IDLE • awaiting host I/O';
  const n=sessionNames.length;
  return `ACTIVE • ${esc(sessionNames[0])}${n>1?` (+${n-1} more)`:''} • open on BioSSD`;
}
function kv(k,v,green=false){return `<div class="kv"><span>${k}</span><b class="${green?'green':''}" title="${esc(v)}">${esc(v)}</b></div>`}
function setProgress(tx){
  const pr=document.querySelector('#progress');
  if(!tx){pr.textContent='—';pr.className='right';return}
  pr.textContent=`${tx.progress}%`;
  const done=tx.status==='COMPLETE',bad=tx.status==='FAILED';
  pr.classList.toggle('complete',done);
  pr.classList.toggle('bad',bad);
  pr.classList.toggle('live',!done&&!bad);
}
function showIdleTelemetry(){
  document.querySelector('#tele').innerHTML=kv('Transaction','NO ACTIVE TRANSACTION')+kv('Status','AWAITING HOST / UMDI OPERATION');
  cmPanel.classList.remove('hidden');
  setProgress(null);
}
function showLiveTelemetry(tx){
  cmPanel.classList.add('hidden');
  setProgress(tx);
  document.querySelector('#tele').innerHTML=[
    kv('TRANSACTION ID',tx.id),
    kv('OP / OBJECT',`${tx.op} :: ${tx.name||'—'}`),
    kv('SIZE / BLOCKS',`${fmt(tx.bytes)} (${tx.blocks} blocks)`),
    kv('SELECTED BANK',`BANK ${tx.bank}`),
    kv('MOLECULAR CHANNELS',`${tx.channels} active`),
    kv('CACHE STAGING',tx.cache_state),
    kv('INTEGRITY / ECC',tx.ecc_state,tx.ecc_state==='PASS'),
    kv('PERSISTENCE',tx.persistence_state,tx.persistence_state==='MOLECULAR_PERSISTENT'),
    kv('MESSAGE / STATUS',tx.message||tx.status)].join('');
}
async function refresh(){try{
  let s=await api('/api/status'),m=s.mount||{},tx=s.transactions[0];
  const dot=document.querySelector('#dot'),faultCount=s.faulted_channels.length;
  dot.className='dot on';
  document.querySelector('#conn').innerHTML=m.mounted
    ? `<b>CONNECTED</b> | BioSSD (${esc(m.drive)}) • MOUNTED`
    : `<b>CONNECTED</b>`;
  document.querySelector('#used').textContent=fmt(s.molecular_used_bytes);
  document.querySelector('#cache').textContent=fmt(s.cache_free_bytes);
  document.querySelector('#channels').textContent=s.healthy_channels.toLocaleString();
  document.querySelector('#namespace').textContent=`${fmt(s.molecular_used_bytes)} used • ${fmt(s.molecular_free_bytes)} free • ${s.files.length} persistent object${s.files.length===1?'':'s'}`;
  renderDevObjects(s.files);

  /* open-handle session: a doc/video/game held open counts as ACTIVE regardless of transaction state */
  const sess=s.session||{active:false,names:[]};
  sessionBusy=!!sess.active;
  sessionNames=sess.names||[];
  faultCountGlobal=faultCount;
  applyDeviceVisual();

  if(tx&&tx.id!==lastId){lastId=tx.id;isActive=true;clearTimeout(idleTimer);replay(tx,faultCount);
    idleTimer=setTimeout(()=>goIdle(faultCount),(tx.events?.length||1)*STEP_MS+HOLD_MS)}
  if(isActive&&tx){
    document.querySelector('#txheadline').textContent=`${tx.op} • ${tx.name||'—'} • ${tx.status}`;
    showLiveTelemetry(tx);
  }else{
    document.querySelector('#txheadline').textContent=sessionBusy?fileOpenHeadline():(tx?`IDLE • last transaction: ${tx.op} (${tx.status})`:'IDLE • awaiting host I/O');
    showIdleTelemetry();
  }
  let txHtml=s.transactions.slice(0,8).map(t=>`<div class="tx"><span class="op">${t.op}</span><span class="name" title="${esc(t.name)}">${esc(t.name)}</span><span class="${t.status==='FAILED'?'bad':'done'}">${t.status==='COMPLETE'?'PASS':t.status}</span></div>`).join('');
  document.querySelector('#txs').innerHTML=txHtml||'<div class="empty">No transactions recorded</div>';
}catch(e){document.querySelector('#dot').className='dot';console.error(e)}}
function toggleDev(show){document.querySelector('#devdrawer').classList.toggle('open',show)}
async function diagWrite(){let f=document.querySelector('#devfile').files[0],n=document.querySelector('#devname').value.trim()||f?.name;if(!f)return alert('Select a file first');let b=await f.arrayBuffer();try{let r=await api(`/api/write?name=${encodeURIComponent(n)}`,{method:'POST',body:b});document.querySelector('#devmsg').textContent=`WRITE PASS: ${r.file.name}`}catch(e){document.querySelector('#devmsg').textContent=`WRITE ERROR: ${e.message}`}}
function renderDevObjects(files){
  const el=document.querySelector('#devobjects');
  if(!files?.length){el.innerHTML='No persistent objects stored.';return}
  el.innerHTML=files.map(f=>`<div style="display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px;align-items:center;padding:3px 0;border-bottom:1px dotted rgba(70,130,145,.2)"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(f.name)}">${esc(f.name)}</span><span>${fmt(f.size)}</span><button class="btn" style="padding:3px 7px;font-size:7px;white-space:nowrap" onclick="diagRead('${encodeURIComponent(f.name)}')">READ / DOWNLOAD</button></div>`).join('');
}
async function diagRead(encodedName){
  const name=decodeURIComponent(encodedName);
  const msg=document.querySelector('#devmsg');
  try{
    msg.textContent=`READ requested: ${name}`;
    const r=await fetch(`/api/read?name=${encodeURIComponent(name)}`);
    if(!r.ok)throw Error(await r.text());
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    msg.textContent=`READ PASS: ${name}`;
  }catch(e){msg.textContent=`READ ERROR: ${e.message}`}
}
async function fault(){let c=document.querySelector('#ch').value;if(c==='')return;await api(`/api/fault?channel=${c}`,{method:'POST'});refresh()}
async function clearFaults(){await api('/api/clear_faults',{method:'POST'});refresh()}
renderStages('WRITE',-1);showIdleTelemetry();refresh();setInterval(refresh,500);
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(INDEX.encode())
        elif p.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(HOST.send('GET_STATUS')).encode())
        elif p.path == '/api/read':
            qs = parse_qs(p.query)
            name = qs.get('name', [''])[0]
            if not name:
                self.send_error(400, 'Missing object name')
                return
            try:
                data, meta = HOST.send('READ', name=name)
                content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Disposition', f'attachment; filename="{Path(name).name}"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_error(404, 'Object not found')
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        qs = parse_qs(p.query)
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length > 0 else b''
            if p.path == '/api/write':
                name = qs.get('name', ['unnamed'])[0]
                res = HOST.send('WRITE', binary=body, name=name)
                self._json(res)
            elif p.path == '/api/fault':
                ch = int(qs.get('channel', [0])[0])
                res = HOST.send('INJECT_FAULT', channel=ch)
                self._json(res)
            elif p.path == '/api/clear_faults':
                res = HOST.send('CLEAR_FAULTS')
                self._json(res)
            else:
                self.send_error(404)
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass

def run():
    server = ThreadingHTTPServer(('127.0.0.1', 8080), Handler)
    print("UMDI Server running on http://127.0.0.1:8080")
    webbrowser.open('http://127.0.0.1:8080')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")

if __name__ == '__main__':
    run()