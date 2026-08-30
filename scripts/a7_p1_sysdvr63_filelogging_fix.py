from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')

# Official SysDVR 6.3 has a dormant FILE_LOGGING printf type bug: res.Result is
# a struct, while the log message intends to print its Code field.
p = root / 'source/modes/proto.c'
s = p.read_text()
old = 'LOG("Handshake failed with code %d\\n", res.Result);'
new = 'LOG("Handshake failed with code %d\\n", res.Result.Code);'
if old not in s:
    raise SystemExit('SysDVR 6.3 proto FILE_LOGGING format anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# A7-P1 deliberately downgrades per-packet UDP logging to LOG_V. In a normal
# FILE_LOGGING build VERBOSE_LOGGING is off, so explicitly mark the temporary
# pointer used only by that diagnostic as consumed.
p = root / 'source/ncm/ncm_device.c'
s = p.read_text()
old = '''        const u8* udp = ip + ihl;
        LOG_V("A7-P1 IPv4 UDP: %u -> %u\\n", _be16(udp), _be16(udp + 2));
        _handleDhcp(eth, ethLen, ip, ihl);
'''
new = '''        const u8* udp = ip + ihl;
        LOG_V("A7-P1 IPv4 UDP: %u -> %u\\n", _be16(udp), _be16(udp + 2));
        (void)udp;
        _handleDhcp(eth, ethLen, ip, ihl);
'''
if old not in s:
    raise SystemExit('A7-P1 UDP verbose-log anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

print('SysDVR 6.3 + A7-P1 FILE_LOGGING compile fixes applied')
