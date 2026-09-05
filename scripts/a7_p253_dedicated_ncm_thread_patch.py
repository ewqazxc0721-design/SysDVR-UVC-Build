from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.5.3 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


# Preserve P2.5.2's staged USB transition while advancing the runtime marker.
core = root / "source/core.c"
s = core.read_text(encoding="utf-8")
if s.count("A7-P2.5.2") != 6 or s.count("BUILD=P252") != 1:
    raise SystemExit("P2.5.3 core markers do not match the P2.5.2 base")
s = s.replace("A7-P2.5.2", "A7-P2.5.3")
s = s.replace("BUILD=P252", "BUILD=P253")
core.write_text(s, encoding="utf-8")


ncm_mode = root / "source/ncm/NCMmode.c"
s = ncm_mode.read_text(encoding="utf-8")
if s.count("A7-P2.5.2") != 10 or s.count("BUILD=P252") != 3:
    raise SystemExit("P2.5.3 NCM mode markers do not match the P2.5.2 base")
s = s.replace("A7-P2.5.2", "A7-P2.5.3")
s = s.replace("BUILD=P252", "BUILD=P253")
s = replace_once(
    s,
    "DISCOVERY=UDP19999 AUDIO_PUMP=1\\n",
    "DISCOVERY=UDP19999 NET_THREAD=1 NET_POLL_US=500\\n",
    "startup feature marker",
)
s = replace_once(
    s,
    "static atomic_bool g_ncmModeActive = false;\n",
    "static atomic_bool g_ncmModeActive = false;\n"
    "static Thread g_a7p253NetworkThread;\n"
    "static u8 alignas(0x1000) g_a7p253NetworkStack[0x4000 + LOGGING_STACK_BOOST];\n\n"
    "static void A7P253NetworkThread(void* arg)\n"
    "{\n"
    "    (void)arg;\n"
    "    LOG(\"A7-P2.5.3 dedicated NCM network thread started poll=500us\\n\");\n"
    "    while (atomic_load(&g_ncmModeActive)) {\n"
    "        NcmDeviceProcessRequests();\n"
    "        svcSleepThread(500000ULL);\n"
    "    }\n"
    "    LOG(\"A7-P2.5.3 dedicated NCM network thread stopped\\n\");\n"
    "}\n",
    "dedicated network thread",
)
s = replace_once(
    s,
    "    atomic_store(&g_ncmModeActive, true);\n"
    "    LOG(\"A7-P2.5.3 iPad USB ready BUILD=P253: DHCP 192.168.55.1/55.2 TCP video=9911 audio=9922 protocol=03\\n\");",
    "    atomic_store(&g_ncmModeActive, true);\n"
    "    memset(g_a7p253NetworkStack, 0, sizeof(g_a7p253NetworkStack));\n"
    "    LaunchThread(&g_a7p253NetworkThread, A7P253NetworkThread, NULL,\n"
    "                 g_a7p253NetworkStack, sizeof(g_a7p253NetworkStack), 0x2B);\n"
    "    LOG(\"A7-P2.5.3 iPad USB ready BUILD=P253: DHCP 192.168.55.1/55.2 TCP video=9911 audio=9922 protocol=03\\n\");",
    "network thread launch",
)
s = replace_once(
    s,
    "static void NCM_Exit(void)\n"
    "{\n"
    "    NcmDeviceExit();\n"
    "    atomic_store(&g_ncmModeActive, false);",
    "static void NCM_Exit(void)\n"
    "{\n"
    "    atomic_store(&g_ncmModeActive, false);\n"
    "    JoinThread(&g_a7p253NetworkThread);\n"
    "    NcmDeviceExit();",
    "network thread shutdown ordering",
)
s = replace_once(
    s,
    "        // The video capture call may block before the audio socket has\n"
    "        // completed its handshake. Let the audio worker service USB/NCM\n"
    "        // too, so TCP 9922 cannot starve behind video capture.\n"
    "        NcmDeviceProcessRequests();\n\n",
    "",
    "remove audio-thread request pump",
)
s = replace_once(
    s,
    "    while (atomic_load(&IsThreadRunning)) {\n"
    "        NcmDeviceProcessRequests();\n\n"
    "        if (!NcmDeviceVideoSessionReady()) {",
    "    while (atomic_load(&IsThreadRunning)) {\n"
    "        if (!NcmDeviceVideoSessionReady()) {",
    "remove video-thread request pump",
)
ncm_mode.write_text(s, encoding="utf-8")


ncm_device = root / "source/ncm/ncm_device.c"
s = ncm_device.read_text(encoding="utf-8")
if s.count("A7-P2.5.2") != 6:
    raise SystemExit("P2.5.3 NCM device markers do not match the P2.5.2 base")
s = s.replace("A7-P2.5.2", "A7-P2.5.3")
s = replace_once(
    s,
    "#include <string.h>\n#include <switch.h>",
    "#include <string.h>\n#include <stdatomic.h>\n#include <switch.h>",
    "atomic include",
)

# The network thread owns all USB OUT polling. Stream workers only enqueue IN
# traffic. Atomic TCP scalars make ACK/session state safe across those workers.
for old, new in (
    ("static A7TcpState g_tcpState", "static _Atomic A7TcpState g_tcpState"),
    ("static u16 g_tcpClientPort", "static _Atomic u16 g_tcpClientPort"),
    ("static u32 g_tcpClientNextSeq", "static _Atomic u32 g_tcpClientNextSeq"),
    ("static u32 g_tcpServerNextSeq", "static _Atomic u32 g_tcpServerNextSeq"),
    ("static u32 g_tcpServerAckedSeq", "static _Atomic u32 g_tcpServerAckedSeq"),
    ("static bool g_tcpHelloSent", "static _Atomic bool g_tcpHelloSent"),
    ("static bool g_tcpProtoReady", "static _Atomic bool g_tcpProtoReady"),
    ("static A7TcpState g_audioTcpState", "static _Atomic A7TcpState g_audioTcpState"),
    ("static u16 g_audioTcpClientPort", "static _Atomic u16 g_audioTcpClientPort"),
    ("static u32 g_audioTcpClientNextSeq", "static _Atomic u32 g_audioTcpClientNextSeq"),
    ("static u32 g_audioTcpServerNextSeq", "static _Atomic u32 g_audioTcpServerNextSeq"),
    ("static u32 g_audioTcpServerAckedSeq", "static _Atomic u32 g_audioTcpServerAckedSeq"),
    ("static bool g_audioTcpHelloSent", "static _Atomic bool g_audioTcpHelloSent"),
    ("static bool g_audioTcpProtoReady", "static _Atomic bool g_audioTcpProtoReady"),
):
    s = replace_once(s, old, new, old)

if s.count("        NcmDeviceProcessRequests();\n") != 4:
    raise SystemExit("P2.5.3 expected four stream-side request pumps")
s = s.replace("        NcmDeviceProcessRequests();\n", "")
ncm_device.write_text(s, encoding="utf-8")

print("A7-P2.5.3 dedicated NCM network thread patch applied")
