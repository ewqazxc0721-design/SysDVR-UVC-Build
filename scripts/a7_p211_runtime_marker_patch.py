from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')

# A7-P2.1.1 is a deployment-verification build. A user's post-P2.1 test log
# still identified the older A7-P2/4096-byte binary, so the loaded build must
# be unmistakable before performance conclusions are drawn.

p = root / 'source/core.c'
s = p.read_text()
core_old = 'LOG("A7-P2 raw SD logfile initialized\\n");'
core_new = 'LOG("A7-P2.1.1 raw SD logfile initialized BUILD=P211\\n");'
if core_old not in s:
    raise SystemExit('P2.1.1 core logfile marker anchor missing')
s = s.replace(core_old, core_new, 1)
p.write_text(s)

p = root / 'source/ncm/ncm_device.c'
s = p.read_text()
# P2.1 must have removed this hot-path success log entirely.
if 'LOG("A7-P1 NCM OUT armed' in s:
    raise SystemExit('P2.1.1 refuses build: hot-path NCM OUT armed log still present')
# If the historical ready string lives in this translation unit, version it;
# some patch chains place the ready banner elsewhere, so this is optional.
if 'A7-P2.1 NCM ready:' in s:
    s = s.replace('A7-P2.1 NCM ready:',
                  'A7-P2.1.1 NCM ready: NTB=16384 datagrams=8 BUILD=P211;', 1)
elif 'A7-P2 NCM ready:' in s:
    s = s.replace('A7-P2 NCM ready:',
                  'A7-P2.1.1 NCM ready: NTB=16384 datagrams=8 BUILD=P211;', 1)
p.write_text(s)

p = root / 'source/ncm/NCMmode.c'
s = p.read_text()
old = 'A7-P2.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting'
new = 'A7-P2.1.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting BUILD=P211 NTB=16384 DATAGRAMS=8'
if old not in s:
    raise SystemExit('P2.1.1 mode banner anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

print('A7-P2.1.1 runtime/deployment markers applied')
