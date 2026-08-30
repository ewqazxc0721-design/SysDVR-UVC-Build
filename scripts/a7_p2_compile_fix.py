from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')
p = root / 'source/ncm/NCMmode.c'
s = p.read_text()
if '#include <string.h>' not in s:
    s = s.replace('#include <switch.h>\n', '#include <switch.h>\n#include <string.h>\n', 1)
p.write_text(s)
print('A7-P2 compile fix applied: NCMmode string.h')
