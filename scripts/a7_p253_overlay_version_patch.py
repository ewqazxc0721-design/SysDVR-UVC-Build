from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr-overlay")
makefile = root / "Makefile"
text = makefile.read_text(encoding="utf-8")
old = "APP_VERSION :=\t1.0.17-P252"
new = "APP_VERSION :=\t1.0.18-P253"
if text.count(old) != 1:
    raise SystemExit(f"P2.5.3 overlay version: expected one anchor, found {text.count(old)}")
makefile.write_text(text.replace(old, new, 1), encoding="utf-8")
print("A7-P2.5.3 overlay version marker applied")
