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
# Replace only the scratch frame inside these two TCP builders; the other P0.2
# DHCP/ARP/ICMP helpers intentionally keep using the validated shared scratch.
p = root / 'source/ncm/ncm_device.c'
s = p.read_text()
old = '    u8* f = g_p02Frame;\n'
new = '    u8 fbuf[14 + 20 + 20 + A7P1_TCP_MSS];\n    u8* f = fbuf;\n'

def patch_tcp_builder(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f'A7-P2 TCP builder signature missing: {signature}')
    end = text.find('\nstatic ', start + len(signature))
    if end < 0:
        end = len(text)
    pos = text.find(old, start, end)
    if pos < 0:
        raise SystemExit(f'A7-P2 TCP shared-frame anchor missing in: {signature}')
    if text.find(old, pos + len(old), end) >= 0:
        raise SystemExit(f'A7-P2 multiple shared-frame anchors in: {signature}')
    return text[:pos] + new + text[pos + len(old):]

s = patch_tcp_builder(
    s,
    'static Result _tcpSendSegment(u32 seq, u32 ack, u8 flags, const void* payload, u16 payloadLen)\n'
)
s = patch_tcp_builder(
    s,
    'static Result _audioTcpSendSegment(u32 seq, u32 ack, u8 flags, const void* payload, u16 payloadLen)\n'
)
p.write_text(s)

print('A7-P2 compile/concurrency fixes applied')
