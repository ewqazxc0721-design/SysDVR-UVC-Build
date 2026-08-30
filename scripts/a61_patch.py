from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a61')

# --- uvc_device.c: native H264 descriptors + split usb:ds appends ---
p = root / 'source/uvc/uvc_device.c'
s = p.read_text()
start = s.index('// Video Streaming Class-Specific Descriptors\n')
end = s.index('// Bulk endpoint for video streaming\n', start)
replacement = '''// Video Streaming Class-Specific Descriptors
// A6.1: native UVC 1.5 H.264 descriptors. Appended one descriptor at a time
// because usb:ds rejects the combined native-H264 VS block with ResultInvalidSize.
static struct __attribute__((packed)) {
    UvcVsInputHeaderDescriptor inputHeader;
    UvcFormatH264Descriptor h264Format;
    UvcFrameH264Descriptor h264Frame;
    UvcColorMatchingDescriptor colorMatching;
} g_vsDescriptors = {
    .inputHeader = {
        .bLength = sizeof(UvcVsInputHeaderDescriptor),
        .bDescriptorType = UVC_CS_INTERFACE,
        .bDescriptorSubType = UVC_VS_INPUT_HEADER,
        .bNumFormats = 1,
        .wTotalLength = sizeof(g_vsDescriptors),
        .bEndpointAddress = USB_ENDPOINT_IN | 1,
        .bmInfo = 0,
        .bTerminalLink = UVC_ENTITY_OUTPUT_TERMINAL,
        .bStillCaptureMethod = 0,
        .bTriggerSupport = 0,
        .bTriggerUsage = 0,
        .bControlSize = 1,
        .bmaControls1 = 0,
    },
    .h264Format = {
        .bLength = sizeof(UvcFormatH264Descriptor),
        .bDescriptorType = UVC_CS_INTERFACE,
        .bDescriptorSubType = UVC_VS_FORMAT_H264,
        .bFormatIndex = 1,
        .bNumFrameDescriptors = 1,
        .bDefaultFrameIndex = 1,
        .bMaxCodecConfigDelay = 1,
        .bmSupportedSliceModes = 0,
        .bmSupportedSyncFrameTypes = 0x03,
        .bResolutionScaling = 0,
        .Reserved1 = 0,
        .bmSupportedRateControlModes = 0x01,
        .wMaxMBperSecOneResolutionNoScalability = 108,
    },
    .h264Frame = {
        .bLength = sizeof(UvcFrameH264Descriptor),
        .bDescriptorType = UVC_CS_INTERFACE,
        .bDescriptorSubType = UVC_VS_FRAME_H264,
        .bFrameIndex = 1,
        .wWidth = UVC_VIDEO_WIDTH,
        .wHeight = UVC_VIDEO_HEIGHT,
        .wSARwidth = 1,
        .wSARheight = 1,
        .wProfile = 0x640C,
        .bLevelIDC = 0x20,
        .wConstrainedToolset = 0,
        .bmSupportedUsages = 0x00010003,
        .bmCapabilities = 0x002A,
        .bmSVCCapabilities = 0,
        .bmMVCCapabilities = 0,
        .dwMinBitRate = UVC_MIN_BITRATE,
        .dwMaxBitRate = UVC_MAX_BITRATE,
        .dwDefaultFrameInterval = UVC_FRAME_INTERVAL_30FPS,
        .bNumFrameIntervals = 1,
        .dwFrameInterval1 = UVC_FRAME_INTERVAL_30FPS,
    },
    .colorMatching = {
        .bLength = sizeof(UvcColorMatchingDescriptor),
        .bDescriptorType = UVC_CS_INTERFACE,
        .bDescriptorSubType = UVC_VS_COLORFORMAT,
        .bColorPrimaries = 1,
        .bTransferCharacteristics = 1,
        .bMatrixCoefficients = 4,
    },
};

'''
s = s[:start] + replacement + s[end:]

old = "    ctrl->bPreferedVersion = 1;\n    ctrl->bMinVersion = 1;\n    ctrl->bMaxVersion = 1;\n"
new = old + "    ctrl->bUsage = 0x01;\n    ctrl->bBitDepthLuma = 8;\n    ctrl->bmSettings = 0x2A;\n    ctrl->bMaxNumberOfRefFramesPlus1 = 2;\n    ctrl->bmRateControlModes = 0x0001;\n    ctrl->bmLayoutPerStream = 0;\n"
if old not in s:
    raise SystemExit('probe/commit anchor missing')
s = s.replace(old, new, 1)

high = '''    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_High, &g_vsDescriptors, sizeof(g_vsDescriptors)));
'''
high_new = '''    // A6.1: split class-specific VS descriptors to stay within usb:ds per-call size.
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_High, &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_High, &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_High, &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_High, &g_vsDescriptors.colorMatching, sizeof(g_vsDescriptors.colorMatching)));
'''
super_old = '''    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_Super, &g_vsDescriptors, sizeof(g_vsDescriptors)));
'''
super_new = '''    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_Super, &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_Super, &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_Super, &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame)));
    R_RET_ON_FAIL(usbDsInterface_AppendConfigurationData(g_uvcCtx.streamingInterface,
        UsbDeviceSpeed_Super, &g_vsDescriptors.colorMatching, sizeof(g_vsDescriptors.colorMatching)));
'''
if high not in s or super_old not in s:
    raise SystemExit('VS append anchors missing')
s = s.replace(high, high_new, 1).replace(super_old, super_new, 1)

anchor = 'static Result _setupInterfaces5x(void)\n{\n    Result rc = 0;\n'
repl = anchor + '    LOG("A6.1 VS sizes: header=%u format=%u frame=%u color=%u total=%u\\n", (unsigned)sizeof(g_vsDescriptors.inputHeader), (unsigned)sizeof(g_vsDescriptors.h264Format), (unsigned)sizeof(g_vsDescriptors.h264Frame), (unsigned)sizeof(g_vsDescriptors.colorMatching), (unsigned)sizeof(g_vsDescriptors));\n'
if anchor not in s:
    raise SystemExit('setup interface anchor missing')
s = s.replace(anchor, repl, 1)
p.write_text(s)

# --- main.c: A5-style raw state monitor ---
p = root / 'source/sysmodule/main.c'
s = p.read_text()
marker = 'static volatile bool g_running = true;\n'
helper = '''\n\nstatic const char* A61UsbStateName(UsbState state)
{
    switch (state) {
        case UsbState_Detached: return "Detached";
        case UsbState_Attached: return "Attached";
        case UsbState_Powered: return "Powered";
        case UsbState_Default: return "Default";
        case UsbState_Address: return "Address";
        case UsbState_Configured: return "Configured";
        case UsbState_Suspended: return "Suspended";
        default: return "Unknown";
    }
}
'''
if marker not in s:
    raise SystemExit('g_running marker missing')
s = s.replace(marker, marker + helper, 1)
old_loop = '''    // Main thread just sleeps - all work is done in streaming threads
    while (g_running) {
        svcSleepThread(1E+9);  // 1 second
    }
'''
new_loop = '''    LOG("A6.1-NATIVE-H264-SPLIT-DIAG raw USB monitor active (1ms poll, 1s heartbeat)\\n");
    UsbState lastState = (UsbState)0xFF;
    bool haveState = false;
    u32 heartbeat = 0, seq = 0;
    while (g_running) {
        UsbState state = UsbState_Detached;
        Result rc = usbDsGetState(&state);
        if (R_SUCCEEDED(rc)) {
            if (!haveState || state != lastState) {
                UsbDeviceSpeed speed = UsbDeviceSpeed_None;
                Result speedRc = usbDsGetSpeed(&speed);
                LOG("A6.1 USB state #%u: %u (%s), speed=%u, speedRc=0x%x\\n", seq++, (unsigned)state, A61UsbStateName(state), (unsigned)speed, speedRc);
                lastState = state;
                haveState = true;
            }
            if (++heartbeat >= 1000) {
                LOG("A6.1 heartbeat: state=%u (%s)\\n", (unsigned)state, A61UsbStateName(state));
                heartbeat = 0;
            }
        }
        svcSleepThread(1E+6);
    }
'''
if old_loop not in s:
    raise SystemExit('main loop anchor missing')
s = s.replace(old_loop, new_loop, 1)
p.write_text(s)

# --- core.c: thread-safe FileLogging ---
p = root / 'source/core.c'
s = p.read_text()
old_globals = 'static FsFileSystem g_sdCard;\nstatic bool g_sdMounted = false;\n'
new_globals = 'static FsFileSystem g_sdCard;\nstatic bool g_sdMounted = false;\nstatic Mutex g_fileLogMutex = 0;\n'
if old_globals not in s:
    raise SystemExit('file logging globals marker missing')
s = s.replace(old_globals, new_globals, 1)
start = s.index('void LogFunctionImpl(const char* fmt, ...)\n{', s.index('#elif FILE_LOGGING'))
end = s.index('\n}\n#endif', start) + 2
repl = '''void LogFunctionImpl(const char* fmt, ...)
{
    if (!g_sdMounted) return;
    mutexLock(&g_fileLogMutex);
    char buf[512];
    va_list args;
    va_start(args, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    if (len > 0) {
        FsFile file;
        if (R_SUCCEEDED(fsFsOpenFile(&g_sdCard, "/logfile.txt", FsOpenMode_Write | FsOpenMode_Append, &file))) {
            s64 offset = 0;
            fsFileGetSize(&file, &offset);
            fsFileWrite(&file, offset, buf, len, FsWriteOption_Flush);
            fsFileClose(&file);
        }
    }
    mutexUnlock(&g_fileLogMutex);
}'''
s = s[:start] + repl + s[end:]
p.write_text(s)

print('A6.1 patch applied')
