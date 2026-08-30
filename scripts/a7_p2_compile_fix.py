from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')

# Generated NCM mode needs the declaration for memset.
p = root / 'source/ncm/NCMmode.c'
s = p.read_text()
if '#include <string.h>' not in s:
    s = s.replace('#include <switch.h>\n', '#include <switch.h>\n#include <string.h>\n', 1)
p.write_text(s)

# Video and audio run on different threads. Their TCP segment builders must not
# share the P0.2 scratch Ethernet frame before the USB-IN mutex is acquired.
# Give each call a private stack frame; the shared USB NTB buffer itself remains
# serialized by g_a7p2TxMutex inside _sendNcmEthernetFrameQuiet().
p = root / 'source/ncm/ncm_device.c'
s = p.read_text()
old = '    u8* f = g_p02Frame;\n'
new = '    u8 fbuf[14 + 20 + 20 + A7P1_TCP_MSS];\n    u8* f = fbuf;\n'
count = s.count(old)
if count != 2:
    raise SystemExit(f'A7-P2 expected 2 TCP shared-frame anchors, got {count}')
s = s.replace(old, new)
p.write_text(s)

print('A7-P2 compile/concurrency fixes applied')
