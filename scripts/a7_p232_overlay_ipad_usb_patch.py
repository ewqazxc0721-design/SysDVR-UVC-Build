from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr-overlay")


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.3.2 {label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


main = root / "source/main.cpp"

replace_once(
    main,
    "#define TYPE_MODE_RTSP 4\n#define TYPE_MODE_NULL 3",
    "#define TYPE_MODE_RTSP 4\n#define TYPE_MODE_IPAD_USB 5\n#define TYPE_MODE_NULL 3",
    "iPad USB mode constant",
)

replace_once(
    main,
    '''        auto *usbModeItem = new tsl::elm::ListItem("USB");
        usbModeItem->setClickListener(getModeLambda(TYPE_MODE_USB));
        list->addItem(usbModeItem);

        auto *tcpModeItem''',
    '''        auto *usbModeItem = new tsl::elm::ListItem("USB");
        usbModeItem->setClickListener(getModeLambda(TYPE_MODE_USB));
        list->addItem(usbModeItem);

        auto *ipadUsbModeItem = new tsl::elm::ListItem("iPad USB");
        ipadUsbModeItem->setClickListener(getModeLambda(TYPE_MODE_IPAD_USB));
        list->addItem(ipadUsbModeItem);

        auto *tcpModeItem''',
    "fifth overlay button",
)

replace_once(
    main,
    '''            case TYPE_MODE_RTSP:
                return"RTSP";
            case TYPE_MODE_NULL:''',
    '''            case TYPE_MODE_RTSP:
                return"RTSP";
            case TYPE_MODE_IPAD_USB:
                return"iPad USB";
            case TYPE_MODE_NULL:''',
    "active mode label",
)

replace_once(
    root / "Makefile",
    "APP_VERSION :=\t1.0.15",
    "APP_VERSION :=\t1.0.16-P232",
    "visible overlay version",
)

print("A7-P2.3.2 SysDVR overlay iPad USB button patch applied")
