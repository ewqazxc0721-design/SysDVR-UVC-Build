from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a62')

# Instrument native-H264 VS descriptor appends.
p = root / 'source/uvc/uvc_device.c'
s = p.read_text()
start_marker = '    // Append VS descriptors (single alt 0: interface → VS class-specific → bulk EP)\n'
end_marker = '    // Register the video endpoint on VS\n'
start = s.index(start_marker)
end = s.index(end_marker, start)

block = r'''    // Append VS descriptors (single alt 0: interface → VS class-specific → bulk EP)
    // A6.2: explicit per-append diagnostics to identify usb:ds cumulative-size limit.
#define A62_APPEND(label, iface, speed, ptr, sz, cumulative) do { \
        LOG("A6.2 append %-18s speed=%u size=%u cumulative=%u -> ", label, (unsigned)(speed), (unsigned)(sz), (unsigned)(cumulative)); \
        rc = usbDsInterface_AppendConfigurationData((iface), (speed), (ptr), (sz)); \
        LOG("rc=0x%x\n", rc); \
        if (R_FAILED(rc)) return rc; \
    } while (0)

    // High Speed VS. Cumulative values are bytes appended to this VS interface for this speed.
    A62_APPEND("HS interface", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsInterfaceDesc0, USB_DT_INTERFACE_SIZE, 9);
    A62_APPEND("HS input-header", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader), 23);
    A62_APPEND("HS H264-format", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format), 75);
    A62_APPEND("HS H264-frame", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame), 123);
    A62_APPEND("HS color", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.colorMatching, sizeof(g_vsDescriptors.colorMatching), 129);
    g_videoEndpointDesc.wMaxPacketSize = 512;
    A62_APPEND("HS endpoint", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_videoEndpointDesc, USB_DT_ENDPOINT_SIZE, 136);

    // Super Speed VS.
    A62_APPEND("SS interface", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsInterfaceDesc0, USB_DT_INTERFACE_SIZE, 9);
    A62_APPEND("SS input-header", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader), 23);
    A62_APPEND("SS H264-format", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format), 75);
    A62_APPEND("SS H264-frame", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame), 123);
    A62_APPEND("SS color", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.colorMatching, sizeof(g_vsDescriptors.colorMatching), 129);
    g_videoEndpointDesc.wMaxPacketSize = 1024;
    A62_APPEND("SS endpoint", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_videoEndpointDesc, USB_DT_ENDPOINT_SIZE, 136);
    A62_APPEND("SS companion", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_ssCompanionDesc, sizeof(g_ssCompanionDesc), 142);

#undef A62_APPEND

'''
s = s[:start] + block + s[end:]
s = s.replace('A6.1 VS sizes', 'A6.2 VS sizes')
p.write_text(s)

# Rename A6.1 state-monitor strings in main.c so runtime logs are unambiguous.
p = root / 'source/sysmodule/main.c'
s = p.read_text()
s = s.replace('A6.1-NATIVE-H264-SPLIT-DIAG', 'A6.2-NATIVE-H264-CUMULATIVE-DIAG')
s = s.replace('A6.1 USB state', 'A6.2 USB state')
s = s.replace('A6.1 heartbeat', 'A6.2 heartbeat')
s = s.replace('A6.1 USB get-state ERROR', 'A6.2 USB get-state ERROR')
p.write_text(s)

print('A6.2 instrumentation applied')
