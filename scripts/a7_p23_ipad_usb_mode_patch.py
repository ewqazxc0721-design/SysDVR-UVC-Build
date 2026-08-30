from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr")
sysmodule = root / "sysmodule"
config = root / "SysDVRConfig"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.3 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(replace_once(text, old, new, label), encoding="utf-8")


# Add a fifth mode and IPC command. The original USB mode keeps ID 1 and is
# left untouched; iPad USB gets its own ID 5 and a matching settings button.
p = sysmodule / "source/modes/defines.h"
s = p.read_text(encoding="utf-8")
s = replace_once(s, "#define SYSDVR_IPC_VERSION 18", "#define SYSDVR_IPC_VERSION 19", "IPC version")
s = replace_once(s, "#define TYPE_MODE_NULL 3\n", "#define TYPE_MODE_NULL 3\n#define TYPE_MODE_IPAD_USB 5\n", "mode ID")
s = replace_once(s, "#define CMD_SET_RTSP 4\n", "#define CMD_SET_RTSP 4\n#define CMD_SET_IPAD_USB 5\n", "command ID")
p.write_text(s, encoding="utf-8")

patch(
    sysmodule / "source/modes/modes.h",
    "extern const StreamMode RTSP_MODE;\n",
    "extern const StreamMode RTSP_MODE;\nextern const StreamMode NCM_MODE;\n",
    "mode declaration",
)

p = sysmodule / "source/core.c"
s = p.read_text(encoding="utf-8")
s = replace_once(s, "A7-P2.2.1 raw SD logfile initialized BUILD=P221", "A7-P2.3 raw SD logfile initialized BUILD=P23", "core marker")
s = replace_once(s, "static u8 alignas(0x1000) VStreamStackArea[0x2000 + LOGGING_STACK_BOOST];", "static u8 alignas(0x1000) VStreamStackArea[0x4000 + LOGGING_STACK_BOOST];", "video stack")
s = replace_once(s, "static u8 alignas(0x1000) AStreamStackArea[0x2000 + LOGGING_STACK_BOOST];", "static u8 alignas(0x1000) AStreamStackArea[0x3000 + LOGGING_STACK_BOOST];", "audio stack")
s = replace_once(
    s,
    "\tif (CurrentMode)\n\t{\n\t\tLOG(\"Terminating mode\\n\");",
    "\tif (CurrentMode)\n\t{\n\t\tconst bool exitAfterThreads = CurrentMode == &NCM_MODE;\n\t\tLOG(\"Terminating mode\\n\");",
    "deferred exit flag",
)
s = replace_once(
    s,
    "\t\tif (CurrentMode->ExitFn)\n\t\t\tCurrentMode->ExitFn();",
    "\t\tif (CurrentMode->ExitFn && !exitAfterThreads)\n\t\t\tCurrentMode->ExitFn();",
    "deferred pre-join exit",
)
s = replace_once(
    s,
    "\t\tif (CurrentMode->AThread)\n\t\t\tJoinThread(&AudioThread);\n\n\t\tLOG(\"Terminated\\n\");",
    "\t\tif (CurrentMode->AThread)\n\t\t\tJoinThread(&AudioThread);\n\n\t\tif (CurrentMode->ExitFn && exitAfterThreads)\n\t\t\tCurrentMode->ExitFn();\n\n\t\tLOG(\"Terminated\\n\");",
    "deferred post-join exit",
)
s = replace_once(
    s,
    "\textern bool NcmModeIsActive(void);\n\tif (NcmModeIsActive())\n\t\treturn TYPE_MODE_TCP; // Config app sees the running NCM service as a network mode.\n",
    "",
    "remove NCM TCP disguise",
)
s = replace_once(
    s,
    "\telse if (mode == &RTSP_MODE)\n\t\treturn TYPE_MODE_RTSP;",
    "\telse if (mode == &RTSP_MODE)\n\t\treturn TYPE_MODE_RTSP;\n\telse if (mode == &NCM_MODE)\n\t\treturn TYPE_MODE_IPAD_USB;",
    "current iPad USB mode",
)
s = replace_once(
    s,
    "\tcase TYPE_MODE_RTSP:\n\t\tSwitchModes(&RTSP_MODE);\n\t\tbreak;",
    "\tcase TYPE_MODE_RTSP:\n\t\tSwitchModes(&RTSP_MODE);\n\t\tbreak;\n\tcase TYPE_MODE_IPAD_USB:\n\t\tSwitchModes(&NCM_MODE);\n\t\tbreak;",
    "set iPad USB mode",
)
p.write_text(s, encoding="utf-8")

p = sysmodule / "source/ipc/ipc.c"
s = p.read_text(encoding="utf-8")
s = replace_once(s, "\nextern bool NcmModeIsActive(void);\n", "", "remove NCM IPC guard declaration")
s = replace_once(
    s,
    "\t\tcase CMD_SET_OFF:\n\t\t\tif (NcmModeIsActive()) {\n\t\t\t\tWriteResponseToTLS(ERR_MAIN_SWITCHING);\n\t\t\t\treturn false;\n\t\t\t}\n",
    "\t\tcase CMD_SET_OFF:\n\t\tcase CMD_SET_IPAD_USB:\n",
    "iPad USB IPC command",
)
s = replace_once(
    s,
    "\t\t\t_Static_assert(CMD_SET_OFF == TYPE_MODE_NULL, \"\");",
    "\t\t\t_Static_assert(CMD_SET_OFF == TYPE_MODE_NULL, \"\");\n\t\t\t_Static_assert(CMD_SET_IPAD_USB == TYPE_MODE_IPAD_USB, \"\");",
    "iPad USB IPC mapping",
)
p.write_text(s, encoding="utf-8")

p = sysmodule / "source/sysmodule/main.c"
s = p.read_text(encoding="utf-8")
s = replace_once(s, "\nvoid NcmEntrypoint(void);\n", "", "old NCM entry declaration")
old_worker = '''static Thread g_a7p2NcmThread;
static u8 alignas(0x1000) g_a7p2NcmStack[0x4000 + LOGGING_STACK_BOOST];

static void A7P2NcmThreadMain(void* arg)
{
    (void)arg;
    NcmEntrypoint();
}


'''
s = replace_once(s, old_worker, "", "old permanent NCM worker")
old_boot = '''\t// A7-P1 opt-in mode. With this flag absent the code below is the stock
\t// SysDVR 6.3 TCP/RTSP/original-USB selection path.
\tif (FileExists("/config/sysdvr/ncm")) {
\t\tLOG("A7-P2 NCM flag present; launching NCM worker + IPC server\\n");
\t\tmemset(g_a7p2NcmStack, 0, sizeof(g_a7p2NcmStack));
\t\tLaunchThread(&g_a7p2NcmThread, A7P2NcmThreadMain, NULL,
\t\t             g_a7p2NcmStack, sizeof(g_a7p2NcmStack), 0x2C);
\t\tIpcThread();
\t\treturn 0;
\t}

\tif (FileExists("/config/sysdvr/usb"))
'''
new_boot = '''\tif (FileExists("/config/sysdvr/ipad_usb") || FileExists("/config/sysdvr/ncm"))
\t\tSetModeID(TYPE_MODE_IPAD_USB);
\telse if (FileExists("/config/sysdvr/usb"))
'''
s = replace_once(s, old_boot, new_boot, "boot mode selection")
p.write_text(s, encoding="utf-8")

ncm_mode = r'''#include <switch.h>
#include <string.h>
#include <stdatomic.h>
#include "../core.h"
#include "../capture.h"
#include "../modes/modes.h"
#include "ncm_device.h"

static atomic_bool g_ncmModeActive = false;

bool NcmModeIsActive(void)
{
    return atomic_load(&g_ncmModeActive);
}

static void NCM_Init(void)
{
    LOG("A7-P2.3 iPad USB mode starting BUILD=P23 NTB=16384 DATAGRAMS=8 RING=32 RTO_LOOPS=25 BURST=8\n");

    NcmDeviceConfig cfg = {
        .vendorId = 0x057E,
        .productId = 0x3001,
        .manufacturer = "Nintendo Switch",
        .product = "SysDVR iPad USB NCM",
        .serialNumber = "SysDVR63-IPAD",
    };

    Result rc = NcmDeviceInitialize(&cfg);
    if (R_FAILED(rc)) {
        LOG("A7-P2.3 NcmDeviceInitialize failed: 0x%x\n", rc);
        fatalThrow(rc);
    }

    atomic_store(&g_ncmModeActive, true);
    LOG("A7-P2.3 iPad USB ready BUILD=P23: DHCP 192.168.55.1/55.2 TCP video=9911 audio=9922 protocol=03\n");
}

static void NCM_Exit(void)
{
    NcmDeviceExit();
    atomic_store(&g_ncmModeActive, false);
    LOG("A7-P2.3 iPad USB mode stopped BUILD=P23\n");
}

static void NCM_AudioThread(void* arg)
{
    (void)arg;
    bool wasReady = false;
    u32 packets = 0;

    while (atomic_load(&IsThreadRunning)) {
        if (!NcmDeviceAudioSessionReady()) {
            wasReady = false;
            svcSleepThread(1000000ULL);
            continue;
        }

        if (!wasReady) {
            CaptureAudioConnected();
            wasReady = true;
            packets = 0;
            LOG("A7-P2.3 audio session active; starting grc:d capture\n");
            svcSleepThread(100000000ULL);
        }

        CaptureReadAudio();
        if (!atomic_load(&IsThreadRunning)) continue;
        if (!NcmDeviceAudioSessionReady()) continue;
        const u32 bytes = (u32)sizeof(PacketHeader) + APkt.Header.DataSize;
        if (!NcmDeviceSendAudioPacket(&APkt, bytes)) {
            LOG("A7-P2.3 audio packet send failed; waiting for reconnect\n");
            NcmDeviceAbortAudioSession();
            wasReady = false;
            continue;
        }
        packets++;
        if ((packets % 600u) == 0)
            LOG("A7-P2.3 streamed %u audio packets, last=%u bytes\n", packets, bytes);
    }
}

static void NCM_VideoThread(void* arg)
{
    (void)arg;
    bool wasReady = false;
    u32 frames = 0;

    while (atomic_load(&IsThreadRunning)) {
        NcmDeviceProcessRequests();

        if (!NcmDeviceVideoSessionReady()) {
            wasReady = false;
            svcSleepThread(1000000ULL);
            continue;
        }

        if (!wasReady) {
            CaptureVideoConnected();
            wasReady = true;
            frames = 0;
            LOG("A7-P2.3 video session active; starting grc:d capture\n");
            svcSleepThread(100000000ULL);
        }

        CaptureReadVideo();
        if (!atomic_load(&IsThreadRunning)) continue;
        if (!NcmDeviceVideoSessionReady()) continue;
        const u32 bytes = (u32)sizeof(PacketHeader) + VPkt.Header.DataSize;
        if (!NcmDeviceSendVideoPacket(&VPkt, bytes)) {
            LOG("A7-P2.3 video packet send failed; waiting for reconnect\n");
            NcmDeviceAbortVideoSession();
            wasReady = false;
            continue;
        }
        frames++;
        if ((frames % 600u) == 0)
            LOG("A7-P2.3 streamed %u video packets, last=%u bytes\n", frames, bytes);
    }
}

const StreamMode NCM_MODE = {
    NCM_Init, NCM_Exit,
    NCM_VideoThread, NCM_AudioThread,
    NULL, NULL,
};
'''
ncm_path = sysmodule / "source/ncm/NCMmode.c"
old_ncm = ncm_path.read_text(encoding="utf-8")
if "A7-P2.2.1 SysDVR 6.3 + USB-NCM burst-retransmit" not in old_ncm:
    raise SystemExit("P2.3 NCM mode: P2.2.1 baseline marker missing")
ncm_path.write_text(ncm_mode, encoding="utf-8")

p = config / "source/translaton.hpp"
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''\t\tstd::string ModeUsb = "Use this mode to stream to the SysDVR-Client application via USB.\\n"
\t\t\t"To setup SysDVR-Client on your pc refer to the guide on Github";
''',
    '''\t\tstd::string ModeUsb = "Use this mode to stream to the SysDVR-Client application via USB.\\n"
\t\t\t"To setup SysDVR-Client on your pc refer to the guide on Github";

\t\tstd::string ModeIpadUsbTitle = "iPad USB";
\t\tstd::string ModeIpadUsb = "Stream video and audio directly to the iPad app over a USB-C data cable.\\n"
\t\t\t"This is separate from the original SysDVR USB mode for PC clients.";
''',
    "translation defaults",
)
p.write_text(s, encoding="utf-8")

patch(
    config / "source/translaton.cpp",
    "MainPageTable, ModeUsbTitle, ModeUsb, ModeTcpTitle",
    "MainPageTable, ModeUsbTitle, ModeUsb, ModeIpadUsbTitle, ModeIpadUsb, ModeTcpTitle",
    "translation fields",
)

p = config / "source/Scenes/SceneMain.cpp"
s = p.read_text(encoding="utf-8")
s = replace_once(s, "\tImage::Img ModeUsb;\n", "\tImage::Img ModeUsb;\n\tImage::Img ModeIpadUsb;\n", "iPad USB image")
s = replace_once(s, "\tstd::string UsbDescription;\n", "\tstd::string UsbDescription;\n\tstd::string IpadUsbDescription;\n", "iPad USB description")
s = replace_once(
    s,
    "\t\tif (fs::Exists(SDMC \"/config/sysdvr/usb\"))\n\t\t\treturn TYPE_MODE_USB;",
    "\t\tif (fs::Exists(SDMC \"/config/sysdvr/ipad_usb\") || fs::Exists(SDMC \"/config/sysdvr/ncm\"))\n\t\t\treturn TYPE_MODE_IPAD_USB;\n\t\telse if (fs::Exists(SDMC \"/config/sysdvr/usb\"))\n\t\t\treturn TYPE_MODE_USB;",
    "read iPad USB boot mode",
)
s = replace_once(
    s,
    "\t\tfs::Delete(SDMC \"/config/sysdvr/rtsp\");\n",
    "\t\tfs::Delete(SDMC \"/config/sysdvr/rtsp\");\n\t\tfs::Delete(SDMC \"/config/sysdvr/ipad_usb\");\n\t\tfs::Delete(SDMC \"/config/sysdvr/ncm\");\n",
    "clear iPad USB boot flags",
)
s = replace_once(
    s,
    "\t\t\telse if (mode == TYPE_MODE_RTSP)\n\t\t\t\tfs::WriteFile(SDMC \"/config/sysdvr/rtsp\", { 'a' });",
    "\t\t\telse if (mode == TYPE_MODE_RTSP)\n\t\t\t\tfs::WriteFile(SDMC \"/config/sysdvr/rtsp\", { 'a' });\n\t\t\telse if (mode == TYPE_MODE_IPAD_USB)\n\t\t\t\tfs::WriteFile(SDMC \"/config/sysdvr/ipad_usb\", { 'a' });",
    "write iPad USB boot flag",
)
s = replace_once(s, "\tModeUsb = Image::Img(ASSET(\"ModeUsb.png\"));\n", "\tModeUsb = Image::Img(ASSET(\"ModeUsb.png\"));\n\tModeIpadUsb = Image::Img(ASSET(\"ModeUsb.png\"));\n", "load iPad USB image")
s = replace_once(s, "\tUsbDescription = Strings::Main.ModeUsb;\n", "\tUsbDescription = Strings::Main.ModeUsb;\n\tIpadUsbDescription = Strings::Main.ModeIpadUsb;\n", "load iPad USB text")
usb_button = '''\tImGui::SetCursorPosX(1280 / 2 - ModeButtonW / 2);
\tif (ModeButton(Strings::Main.ModeUsbTitle, UsbDescription, ModeUsb, CurrentMode == TYPE_MODE_USB, BootMode == TYPE_MODE_USB))
\t\tif (!SetMode(TYPE_MODE_USB))
\t\t\treturn;
'''
s = replace_once(
    s,
    usb_button,
    usb_button + '''
\tImGui::SetCursorPosX(1280 / 2 - ModeButtonW / 2);
\tif (ModeButton(Strings::Main.ModeIpadUsbTitle, IpadUsbDescription, ModeIpadUsb, CurrentMode == TYPE_MODE_IPAD_USB, BootMode == TYPE_MODE_IPAD_USB))
\t\tif (!SetMode(TYPE_MODE_IPAD_USB))
\t\t\treturn;
''',
    "iPad USB button",
)
p.write_text(s, encoding="utf-8")

for name, mode_text, mode_title in (
    ("simplifiedChinese.json", "通过 USB-C 数据线将视频和音频直接串流到 iPad App。\\n此模式独立于面向 PC 客户端的原版 SysDVR USB 模式。", "iPad USB 直连"),
    ("traditionalChinese.json", "透過 USB-C 資料線將影片和音訊直接串流到 iPad App。\\n此模式獨立於面向電腦用戶端的原版 SysDVR USB 模式。", "iPad USB 直連"),
):
    p = config / "romfs/strings" / name
    s = p.read_text(encoding="utf-8")
    s = replace_once(
        s,
        '    "ModeUsbTitle": "USB",\n',
        f'    "ModeUsbTitle": "USB",\n    "ModeIpadUsb": "{mode_text}",\n    "ModeIpadUsbTitle": "{mode_title}",\n',
        f"{name} strings",
    )
    p.write_text(s, encoding="utf-8")

print("A7-P2.3 separate iPad USB runtime mode and settings button patch applied")

