from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')

# A7-P2.1.1 is intentionally a deployment-verification build. The previous
# user runtime log still reported the A7-P2/4096-byte implementation, so make
# the loaded binary unmistakable before evaluating P2.1 performance.

p = root / 'source/core.c'
s = p.read_text()
old = 'LOG("A7-P2 raw SD logfile initialized\\n");'
new = 'LOG("A7-P2.1.1 raw SD logfile initialized BUILD=P211\\n");'
if old not in s:
    raise SystemExit('P2.1.1 core logfile marker anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

p = root / 'source/ncm/ncm_device.c'
s = p.read_text()
old = 'A7-P2.1 NCM ready:'
new = 'A7-P2.1.1 NCM ready: NTB=16384 datagrams=8;'
if old not in s:
    raise SystemExit('P2.1.1 NCM ready marker anchor missing')
s = s.replace(old, new, 1)
# P2.1 must have removed this hot-path success log entirely.
if 'LOG("A7-P1 NCM OUT armed' in s:
    raise SystemExit('P2.1.1 refuses build: hot-path NCM OUT armed log still present')
p.write_text(s)

p = root / 'source/ncm/NCMmode.c'
s = p.read_text()
old = 'A7-P2.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting'
new = 'A7-P2.1.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting BUILD=P211'
if old not in s:
    raise SystemExit('P2.1.1 mode banner anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

print('A7-P2.1.1 runtime/deployment markers applied')
