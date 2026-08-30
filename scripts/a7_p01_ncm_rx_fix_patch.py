from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a7p01')
p = root / 'source/usbnet/ncm_device.c'
s = p.read_text()

# usb:ds/libnx only natively models alternate setting 0 for an interface.
# Keep standards-visible NCM alt1 for iPadOS, but also expose the same bulk
# endpoints under alt0 so the Switch device controller has active pipes.
old = '''static struct usb_interface_descriptor g_dataInterfaceDesc0 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    .bNumEndpoints = 0,
'''
new = '''static struct usb_interface_descriptor g_dataInterfaceDesc0 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    // usb:ds only natively activates alt0 endpoints. Duplicate the NCM bulk
    // pipes here while retaining the standards-visible alt1 descriptors below.
    .bNumEndpoints = 2,
'''
if old not in s:
    raise SystemExit('alt0 descriptor anchor missing')
s = s.replace(old, new, 1)

# Keep the POC NTB small while validating the first host->device Ethernet frame.
s = s.replace('#define NCM_NTB_MAX_SIZE    16384u', '#define NCM_NTB_MAX_SIZE    4096u', 1)

old = '''    cum = 0;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
    if (R_FAILED(rc)) return rc;
    g_dataInEndpointDesc.wMaxPacketSize = 512;
    g_dataOutEndpointDesc.wMaxPacketSize = 512;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;

    cum = 0;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
    if (R_FAILED(rc)) return rc;
    g_dataInEndpointDesc.wMaxPacketSize = 1024;
    g_dataOutEndpointDesc.wMaxPacketSize = 1024;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-in-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-out-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
'''
new = '''    cum = 0;
    g_dataInEndpointDesc.wMaxPacketSize = 512;
    g_dataOutEndpointDesc.wMaxPacketSize = 512;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt0-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt0-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt1-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt1-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;

    cum = 0;
    g_dataInEndpointDesc.wMaxPacketSize = 1024;
    g_dataOutEndpointDesc.wMaxPacketSize = 1024;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-in-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-out-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt1-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt1-data-in-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt1-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt1-data-out-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
'''
if old not in s:
    raise SystemExit('data descriptor sequence anchor missing')
s = s.replace(old, new, 1)

old = '''static void _postOut(void)
{
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || g_ctx.outPending || !g_ctx.dataEndpointOut) return;
    memset(g_dataOutBuffer, 0, sizeof(g_dataOutBuffer));
    eventClear(&g_ctx.dataEndpointOut->CompletionEvent);
    Result rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointOut,
        g_dataOutBuffer, sizeof(g_dataOutBuffer), &g_ctx.outUrbId);
    if (R_SUCCEEDED(rc)) {
        g_ctx.outPending = true;
        LOG("A7-P0 NCM OUT armed (%u bytes)\\n", (unsigned)sizeof(g_dataOutBuffer));
    } else {
        LOG("A7-P0 NCM OUT arm failed: 0x%x\\n", rc);
    }
}
'''
new = '''static void _postOut(void)
{
    static Result lastArmRc = 0;
    static bool haveLastArmRc = false;
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || g_ctx.outPending || !g_ctx.dataEndpointOut) return;

    // libnx usbComms waits for usb:ds readiness before every data transfer.
    Result readyRc = usbDsWaitReady(500000000ULL);
    if (R_FAILED(readyRc)) {
        if (!haveLastArmRc || lastArmRc != readyRc) {
            LOG("A7-P0.1 usbDsWaitReady before NCM OUT failed: 0x%x\\n", readyRc);
            lastArmRc = readyRc;
            haveLastArmRc = true;
        }
        return;
    }

    memset(g_dataOutBuffer, 0, sizeof(g_dataOutBuffer));
    eventClear(&g_ctx.dataEndpointOut->CompletionEvent);
    Result rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointOut,
        g_dataOutBuffer, sizeof(g_dataOutBuffer), &g_ctx.outUrbId);
    if (R_SUCCEEDED(rc)) {
        g_ctx.outPending = true;
        haveLastArmRc = false;
        LOG("A7-P0.1 NCM OUT armed (%u bytes)\\n", (unsigned)sizeof(g_dataOutBuffer));
    } else if (!haveLastArmRc || lastArmRc != rc) {
        LOG("A7-P0.1 NCM OUT arm failed: 0x%x\\n", rc);
        lastArmRc = rc;
        haveLastArmRc = true;
    }
}
'''
if old not in s:
    raise SystemExit('_postOut anchor missing')
s = s.replace(old, new, 1)

# Arm RX before announcing link-up so the first DHCP/IPv6/ARP traffic cannot race us.
s = s.replace('''                else {
                    _sendLinkNotifications();
                    _postOut();
                }
''', '''                else {
                    _postOut();
                    _sendLinkNotifications();
                }
''', 1)
s = s.replace('''    _sendLinkNotifications();
    _postOut();
    _pollOut();
''', '''    _postOut();
    _sendLinkNotifications();
    _pollOut();
''', 1)

# Version all remaining P0 log markers after the targeted replacements.
s = s.replace('A7-P0 ', 'A7-P0.1 ')
s = s.replace('A7-P0\\n', 'A7-P0.1\\n')

p.write_text(s)

# Also version the sysmodule banner so runtime logs prove the correct build.
p = root / 'source/sysmodule/main.c'
ms = p.read_text().replace('A7-P0 SysDVR USB-NCM binding diagnostic', 'A7-P0.1 SysDVR USB-NCM RX-fix diagnostic')
ms = ms.replace('A7-P0', 'A7-P0.1')
p.write_text(ms)
