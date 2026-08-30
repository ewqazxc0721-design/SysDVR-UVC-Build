from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')
p = root / 'source/modes/proto.c'
s = p.read_text()
old = 'LOG("Handshake failed with code %d\\n", res.Result);'
new = 'LOG("Handshake failed with code %d\\n", res.Result.Code);'
if old not in s:
    raise SystemExit('SysDVR 6.3 proto FILE_LOGGING format anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)
print('SysDVR 6.3 FILE_LOGGING proto format fix applied')
