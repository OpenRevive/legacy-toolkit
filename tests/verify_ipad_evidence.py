#!/usr/bin/env python3
"""Cross-check physical acceptance artifacts; a measured limitation is explicit."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.artifacts/ipad1'
UDID='7de8488b450394ab6417308beffa23e8c2f95f14'
def load(name):return json.loads((OUT/(name+'.json')).read_text())
def png(name):return struct.unpack('>II',(OUT/(name+'.png')).read_bytes()[16:24])
def check(g):
    if g==1:
        v=load('identity');assert v['udid']==UDID and v['info']['ProductType']=='iPad1,1' and v['info']['ProductVersion']=='5.1.1'
        assert v['pairing']['validated'].startswith('SUCCESS')
        assert all(any(d['serial']==UDID for d in v['usb'][k]) for k in ('devices','physicalDevices'))
    elif g==2:
        v=load('basic');assert v['udid']==UDID and v['nonce']==v['receipt']['text'] and v['receipt']['clicks']>0
        assert v['exit']==7 and v['timeout']==124 and not v['state']['screenLocked']
        assert png('probe-native-result')==(768,1024) and png('probe-vnc-result')==(1024,768)
        o=load('vnc-input-observation');assert o['retained']==(o['actual']==o['requested'])==v['vncKeyboardRetained']
    elif g==3:
        v=load('uikit');assert v['nonce']==v['receipt']['text'] and 'Привет 日本語' in v['nonce']
        assert all(v[k] for k in ('secureRedacted','forgedRejected','consumedRejected','oldPIDRejected'))
        raw=(OUT/'uikit-initial.json').read_text();assert 'OR-SECURE-SENTINEL' not in raw
        assert any(n['secure'] and not n['value'] for n in json.loads(raw)['nodes'])
        assert 'guard-mismatch' in (OUT/'uikit-forged.stderr').read_text()
        assert 'stale-snapshot' in (OUT/'uikit-consumed.stderr').read_text()
        assert load('uikit-before-restart')['pid']!=load('uikit-after-restart')['pid']
    elif g==4:
        v=load('gestures')
        for count in (1,2,5):
            r=v[str(count)];assert r['dispatch']['maxContactsSent']==r['receipt']['maxSimultaneous']==count
            assert r['receipt']['trace'][-1]['active']==0
        assert v['close']['receipt']['pinchScale']<.8 and v['open']['receipt']['pinchScale']>1.5
        s=load('system');assert s['switcher']['switcherVisible'] and not s['dismiss-switcher']['switcherVisible']
        assert s['notifications']['notificationCenterVisible'] and not s['dismiss-notifications']['notificationCenterVisible']
        assert s['alert']['accepted'] and s['lockUnlock'] and s['coverage']['completeForKnownScreenOwners']
        c=load('compatibility');assert c['nativeTapRefused'] and c['unifiedInputRefused'] and c['appScreen']==[0,0,320,480]
    elif g==5:
        v=load('mcp');assert v['ok'] and v['udid']==UDID and v['build']['exitCode']==v['install']['exitCode']==0
        assert 'Привет 日本語' in v['receipt']['text'] and v['touch']['maxSimultaneous']==2 and v['touch']['pinchScale']>1.5
        assert v['touch']['trace'][-1]['active']==0 and v['exit']['exitCode']==7 and v['timeout']['exitCode']==124
        assert v['coverage']['completeForKnownScreenOwners'] and png('mcp-form')==(768,1024)
        assert load('setup-uikit-verified')['ready'] and load('installed-plugin-check')['identical']
        assert load('installed-mcp')['ok'] and load('installed-mcp')['cwd']=='/tmp'
    elif g==6:
        v=load('dns');assert v['receipt']['passed'] and v['receipt']['run']==v['configuration']['run']
        assert v['receipt']['platform']=='iPad' and v['receipt']['ios']=='5.1.1'
        for r in v['receipt']['tests']:
            assert r['http']['status']==r['https']['status']==200 and r['https']['trusted'] and r['https']['tls']=='TLSv1.2'
            assert r['https']['dnsUDP']==r['https']['dnsTCP']
            assert all(r[k].startswith('certificate trust rejected:') for k in ('unknownCA','wrongName'))
        assert all(v['receipt']['run'] in s for s in v['serverLogs'].values())
        assert 'Profile Installed' in load('ca-profile-install')['visibleLabels']
        logs=load('safari-ca-server-log')['lines'];assert 'CPU OS 5_1_1' in logs and ' 200 ' in logs
        assert load('ca-profile-cleanup')['removed'] and not load('ca-profile-cleanup')['remainingLabCA']
        assert load('vk-dns-rollback')['addresses']==load('vk-dns-baseline')['addresses']
        c=load('connectivity');assert c['positive']['connected'] and not c['ct127']['connected'] and c['ct127']['errno']==60
    elif g==7:
        v=load('vk-message-receipt');rows=load('vk-message-sqlite')['messages'];assert len(rows)==2
        texts={r['body'] for r in rows};assert html.unescape(v['outgoing']['body']) in texts and v['incoming']['text'] in texts
        assert all(r['is_read']==1 for r in rows)
        assert any(v['incoming']['id'] in r.get('update_message_ids',[]) and 'CFNetwork/548.' in r['user_agent'] for r in load('vk-message-wire'))
        assert load('vk-binary-verification')['unchanged']
        a=load('vk-archive-inventory');assert len(a)==67 and len({x['path'] for x in a})==67
        assert load('vk10')['state']['frontmostBundleId']!='org.openrevive.vk10.ipad1'  # classified failed launch
        assert load('iamrich')['state']['frontmostBundleId']=='io.ozio.IAmRich'
        assert all(x['exit']==0 for x in load('backend-regressions').values())
        assert load('backend-regressions')['skeleton-http']['result']['http_receipts']>0
    elif g==8:
        v=load('local-regressions');assert len(v)==7 and all(r['exit']==0 for r in v)
        assert load('setup-uikit-verified')['sourceDigest']==load('setup-uikit-final')['sourceDigest']
    elif g==9:
        v=load('cleanup');assert all(v[k] for k in ('iphoneStatePreserved','existingApplicationsPreserved','noUSBRelay','temporaryConnectHelperRemoved','dnsRestored'))
        assert load('safari-cleanup')['temporaryTabClosed'] and load('keyboard-restored')['originalJapaneseKana']
        assert load('ca-profile-cleanup')['originalProfilesPreserved']==load('ca-profiles-baseline')['existing']
        assert load('iphone-before')==load('iphone-after')
    elif g==10:
        report=(ROOT/'docs/ipad1-2026-10-16.md').read_text()
        for phrase in ('VK 1.0','Veency','CT127','Safari','67','320×480','46'):assert phrase in report
        manifest=json.loads((ROOT/'docs/ipad1-evidence/manifest.json').read_text());assert len(manifest['files'])>=10
        for name,digest in manifest['files'].items():
            assert hashlib.sha256((ROOT/'docs/ipad1-evidence'/name).read_bytes()).hexdigest()==digest
        assert manifest['files']['acceptance.json']==hashlib.sha256((ROOT/'docs/ipad1-evidence/acceptance.json').read_bytes()).hexdigest()
    else:raise AssertionError(g)
    print('OPENREVIVE_IPAD_EVIDENCE_G%d_OK'%g)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('gate',type=int,nargs='?',choices=range(1,11));a=p.parse_args()
    for gate in ([a.gate] if a.gate else range(1,11)):check(gate)
