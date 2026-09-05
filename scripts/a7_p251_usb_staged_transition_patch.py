from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.5.1 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


core = root / "source/core.c"
s = core.read_text(encoding="utf-8")

s = replace_once(
    s,
    "A7-P2.5 raw SD logfile initialized BUILD=P25",
    "A7-P2.5.1 raw SD logfile initialized BUILD=P251",
    "core marker",
)

s = replace_once(
    s,
    "static const StreamMode* SwitchModeTarget = NULL;\n",
    "static const StreamMode* SwitchModeTarget = NULL;\n"
    "static bool A7P251UsbTransitionLocked = false;\n\n"
    "static bool A7P251IsUsbBackedMode(const StreamMode* mode)\n"
    "{\n"
    "\treturn mode == &USB_MODE || mode == &NCM_MODE;\n"
    "}\n",
    "USB transition state",
)

s = replace_once(
    s,
    "\tIsModeSwitchPending = false;\n\n\tLOG(\"Mode started\\n\");",
    "\tIsModeSwitchPending = false;\n"
    "\tA7P251UsbTransitionLocked = false;\n\n"
    "\tLOG(\"Mode started\\n\");",
    "release USB transition lock",
)

old_worker = '''static void SwitchModesThreadMain(void*)
{
\t// This will take forever
\tExitCurrentMode();

\t// This is fast
\tEnterTargetMode();
}
'''
new_worker = '''static void SwitchModesThreadMain(void*)
{
\tconst bool stagedUsbTransition = A7P251UsbTransitionLocked;

\t// This can wait for an active capture thread to leave.
\tExitCurrentMode();

\t// USB serial and USB NCM expose different device classes and descriptors.
\t// Keep usb:ds fully released long enough for the host to observe a real
\t// disconnect before the other USB device is enabled.
\tif (stagedUsbTransition)
\t{
\t\tLOG("A7-P2.5.1 USB transition OFF stage: waiting 1000 ms before target init\\n");
\t\tsvcSleepThread(1000000000ULL);
\t\tLOG("A7-P2.5.1 USB transition OFF stage complete; starting locked target\\n");
\t}

\tEnterTargetMode();
}
'''
s = replace_once(s, old_worker, new_worker, "two-stage switch worker")

s = replace_once(
    s,
    "\tLOG(\"Begin mode switch\\n\");\n\tSwitchModeTarget = mode;\n\tif (!IsModeSwitchPending)",
    "\tLOG(\"Begin mode switch\\n\");\n"
    "\tif (IsModeSwitchPending && A7P251UsbTransitionLocked)\n"
    "\t{\n"
    "\t\tLOG(\"A7-P2.5.1 ignored mode request while staged USB transition is locked\\n\");\n"
    "\t\tmutexUnlock(&ModeSwitchingMutex);\n"
    "\t\treturn;\n"
    "\t}\n\n"
    "\tSwitchModeTarget = mode;\n"
    "\tif (!IsModeSwitchPending)",
    "lock repeated mode requests",
)

s = replace_once(
    s,
    "\t\tIsModeSwitchPending = true;\n\n\t\tif (ModeSwitchThread.handle)",
    "\t\tIsModeSwitchPending = true;\n"
    "\t\tA7P251UsbTransitionLocked =\n"
    "\t\t\tA7P251IsUsbBackedMode(CurrentMode) &&\n"
    "\t\t\tA7P251IsUsbBackedMode(mode) &&\n"
    "\t\t\tCurrentMode != mode;\n"
    "\t\tif (A7P251UsbTransitionLocked)\n"
    "\t\t\tLOG(\"A7-P2.5.1 staged USB class transition locked\\n\");\n\n"
    "\t\tif (ModeSwitchThread.handle)",
    "arm USB transition lock",
)

s = replace_once(
    s,
    "void SetModeID(u32 mode)\n{\n\tswitch (mode)",
    "void SetModeID(u32 mode)\n{\n"
    "\tLOG(\"A7-P2.5.1 SetModeID request=%u\\n\", mode);\n"
    "\tswitch (mode)",
    "mode request diagnostic",
)

core.write_text(s, encoding="utf-8")

ncm_mode = root / "source/ncm/NCMmode.c"
s = ncm_mode.read_text(encoding="utf-8")
if s.count("A7-P2.5") != 10:
    raise SystemExit(f"P2.5.1 NCM mode markers: expected 10, found {s.count('A7-P2.5')}")
s = s.replace("A7-P2.5", "A7-P2.5.1")
s = s.replace("BUILD=P25", "BUILD=P251")
ncm_mode.write_text(s, encoding="utf-8")

ncm_device = root / "source/ncm/ncm_device.c"
s = ncm_device.read_text(encoding="utf-8")
if "A7-P2.5 audio fast recovery" not in s or "A7-P2.5 RX stats" not in s:
    raise SystemExit("P2.5.1 P2.5 NCM diagnostics missing")
s = s.replace("A7-P2.5", "A7-P2.5.1")
ncm_device.write_text(s, encoding="utf-8")

print("A7-P2.5.1 staged OFF USB-class transition patch applied")
