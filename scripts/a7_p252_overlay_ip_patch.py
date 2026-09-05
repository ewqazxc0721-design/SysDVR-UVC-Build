from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr-overlay")


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.5.2 overlay {label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


main = root / "source/main.cpp"

replace_once(
    main,
    "#define TYPE_MODE_SWITCHING 999998\n#define TYPE_MODE_ERROR 999999",
    "#define TYPE_MODE_SWITCHING 999998\n"
    "#define TYPE_MODE_ERROR 999999\n"
    "// updateIP() prints the least-significant byte first, matching nifm.\n"
    "#define IPAD_USB_IP_ADDRESS 0x0137A8C0u // 192.168.55.1",
    "fixed iPad USB address",
)

replace_once(
    main,
    "        updateMode(newMode);\n        updateIP(newIp);",
    "        updateMode(newMode);\n"
    "        updateIP(newMode == TYPE_MODE_IPAD_USB ? IPAD_USB_IP_ADDRESS : newIp);",
    "initial displayed address",
)

replace_once(
    main,
    '''    void refreshIp(){
        u32 newIp;
        nifmGetCurrentIpAddress(&newIp);
        updateIP(newIp);
    }
''',
    '''    void refreshIp(){
        if(mode == TYPE_MODE_IPAD_USB){
            updateIP(IPAD_USB_IP_ADDRESS);
            return;
        }
        u32 newIp;
        nifmGetCurrentIpAddress(&newIp);
        updateIP(newIp);
    }
''',
    "mode-aware displayed address",
)

replace_once(
    root / "Makefile",
    "APP_VERSION :=\t1.0.16-P232",
    "APP_VERSION :=\t1.0.17-P252",
    "visible overlay version",
)

print("A7-P2.5.2 overlay fixed iPad USB IP display patch applied")
