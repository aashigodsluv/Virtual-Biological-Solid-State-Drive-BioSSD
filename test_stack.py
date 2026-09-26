import hashlib, os, tempfile
import umdi_app as u

def run():
    name='__umdi_selftest_'+os.urandom(4).hex()+'.bin'; data=os.urandom(700_000)
    w=u.HOST.send('WRITE',binary=data,name=name)
    assert w['file']['size']==len(data)
    assert w['file']['blocks']==3
    assert w['file']['persistence']=='MOLECULAR_PERSISTENT'
    out,r=u.HOST.send('READ',name=name)
    assert out==data
    assert hashlib.sha256(out).hexdigest()==w['file']['sha256']
    v=u.HOST.send('VERIFY',name=name); assert v['ok']
    s=u.HOST.send('GET_STATUS'); assert s['profile']['molecular_capacity_bytes']==215_000_000_000_000_000
    assert s['profile']['electronic_cache_bytes']==1_000_000_000_000
    u.HOST.send('INJECT_FAULT',channel=7); assert 7 in u.HOST.send('GET_STATUS')['faulted_channels']
    u.HOST.send('CLEAR_FAULTS'); assert not u.HOST.send('GET_STATUS')['faulted_channels']
    u.HOST.send('DELETE',name=name)
    print('UMDI full-stack self-test: PASS')
    print('WRITE -> VERIFY -> MOLECULAR_PERSISTENT -> READ -> SHA-256 equality: PASS')
    print('Profile: 1 g DNA / 215 PB | 1 TB cache | 4096 channels | 256 KiB blocks | 4x READ | 8x WRITE')
if __name__=='__main__': run()
