from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr")


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.3.1 {label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Command 5 is an additive extension and does not require changing the public
# Config API version. Keep API 18 so existing clients continue to recognize
# the sysmodule while the bundled settings app can still use CMD_SET_IPAD_USB.
replace_once(
    root / "sysmodule/source/modes/defines.h",
    "#define SYSDVR_IPC_VERSION 19",
    "#define SYSDVR_IPC_VERSION 18",
    "Config API compatibility",
)

replace_once(
    root / "sysmodule/source/core.c",
    "A7-P2.3 raw SD logfile initialized BUILD=P23",
    "A7-P2.3.1 raw SD logfile initialized BUILD=P231",
    "core marker",
)

p = root / "sysmodule/source/ncm/NCMmode.c"
s = p.read_text(encoding="utf-8")
if s.count("A7-P2.3") != 10:
    raise SystemExit(f"P2.3.1 NCM markers: expected 10, found {s.count('A7-P2.3')}")
s = s.replace("A7-P2.3", "A7-P2.3.1")
s = s.replace("BUILD=P23", "BUILD=P231")
p.write_text(s, encoding="utf-8")

print("A7-P2.3.1 Config API v18 compatibility patch applied")

