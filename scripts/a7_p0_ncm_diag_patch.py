from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a7p0')

# -----------------------------------------------------------------------------
# Build wiring: add source/usbnet
# -----------------------------------------------------------------------------
p = root / 'Makefile'
s = p.read_text()
old = 'SOURCES\t\t:=\tsource source/sysmodule source/uvc'
new = 'SOURCES\t\t:=\tsource source/sysmodule source/uvc source/usbnet'
if old not in s:
    raise SystemExit('Makefile SOURCES anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# A7 P0 does not need grc:d/capture at all. Keep Core only for logging + serial.
# -----------------------------------------------------------------------------
p = root / 'source/core.c'
s = p.read_text()
old = '''    rc = CaptureInitialize();
    if (R_FAILED(rc)) {
        LOG("CaptureInitialize failed: 0x%x\\n", rc);
        return rc;
    }

    g_coreInitialized = true;
    LOG("Core initialization complete\\n");
'''
new = '''    // A7-P0 is a pure USB-NCM binding diagnostic. Do not acquire grc:d here:
    // this lets us validate iPadOS USB Ethernet independently of game DVR support.
    g_coreInitialized = true;
    LOG("Core initialization complete (A7-P0 no capture)\\n");
'''
if old not in s:
    raise SystemExit('CoreInit capture anchor missing')
s = s.replace(old, new, 1)
s = s.replace('    CaptureFinalize();\n\n', '    // A7-P0: capture was never initialized.\n\n', 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# USB NCM public header
# -----------------------------------------------------------------------------
usbnet = root / 'source/usbnet'
usbnet.mkdir(parents=True, exist_ok=True)
(usbnet / 'ncm_device.h').write_text(r'''#pragma once
#include <switch.h>

typedef struct {
    u16 vendorId;
    u16 productId;
    const char* manufacturer;
    const char* product;
    const char* serialNumber;
} NcmDeviceConfig;

Result NcmDeviceInitialize(const NcmDeviceConfig* config);
void NcmDeviceProcessRequests(void);
void NcmDeviceExit(void);
''')

# -----------------------------------------------------------------------------
# Minimal CDC-NCM device implementation.
# Goal P0: descriptor binding + EP0 negotiation + link notifications + RX logging.
# No TCP/IP stack yet.
# Descriptor layout follows TinyUSB CDC-NCM template:
# IAD + CDC control + Header + Union + Ethernet + NCM + interrupt EP,
# then CDC-Data alt0 + alt1 + bulk IN/OUT.
# -----------------------------------------------------------------------------
(usbnet / 'ncm_device.c').write_text(r'''#include <string.h>
#include <switch.h>
#include "ncm_device.h"
#include "../core.h"

#ifndef USB_CLASS_MISC
#define USB_CLASS_MISC 0xEF
#endif

#define USB_CLASS_CDC          0x02
#define USB_CLASS_CDC_DATA     0x0A
#define CDC_SUBCLASS_NCM       0x0D
#define NCM_DATA_PROTOCOL_NTB  0x01
#define USB_CS_INTERFACE       0x24

#define CDC_FUNC_HEADER        0x00
#define CDC_FUNC_UNION         0x06
#define CDC_FUNC_ETHERNET      0x0F
#define CDC_FUNC_NCM           0x1A

#define USB_REQUEST_GET_INTERFACE 0x0A
#define USB_REQUEST_SET_INTERFACE 0x0B

#define NCM_SET_ETHERNET_PACKET_FILTER 0x43
#define NCM_GET_NTB_PARAMETERS         0x80
#define NCM_GET_NTB_FORMAT             0x83
#define NCM_SET_NTB_FORMAT             0x84
#define NCM_GET_NTB_INPUT_SIZE         0x85
#define NCM_SET_NTB_INPUT_SIZE         0x86

#define CDC_NOTIF_NETWORK_CONNECTION       0x00
#define CDC_NOTIF_CONNECTION_SPEED_CHANGE  0x2A

#define NCM_CAP_ETH_FILTER      (1u << 0)
#define NCM_CAP_NTB_INPUT_SIZE  (1u << 5)
#define NCM_CAPABILITIES        (NCM_CAP_ETH_FILTER | NCM_CAP_NTB_INPUT_SIZE)

#define NCM_MTU            1514
#define NCM_NTB_MAX_SIZE    16384u
#define NCM_MAX_DATAGRAMS   1u
#define EP0_TIMEOUT_NS      1000000000ULL
#define EP_TIMEOUT_NS       500000000ULL

#define NTH16_SIGNATURE     0x484D434Eu
#define NDP16_SIGNATURE_0   0x304D434Eu
#define NDP16_SIGNATURE_1   0x314D434Eu

typedef struct __attribute__((packed)) {
    u8 bmRequestType;
    u8 bRequest;
    u16 wValue;
    u16 wIndex;
    u16 wLength;
} UsbSetupPacket;

typedef struct __attribute__((packed)) {
    u8 bLength;
    u8 bDescriptorType;
    u8 bFirstInterface;
    u8 bInterfaceCount;
    u8 bFunctionClass;
    u8 bFunctionSubClass;
    u8 bFunctionProtocol;
    u8 iFunction;
} IadDescriptor;

typedef struct __attribute__((packed)) {
    u8 bLength;
    u8 bDescriptorType;
    u8 bDescriptorSubType;
    u16 bcdCDC;
} CdcHeaderDescriptor;

typedef struct __attribute__((packed)) {
    u8 bLength;
    u8 bDescriptorType;
    u8 bDescriptorSubType;
    u8 bMasterInterface;
    u8 bSlaveInterface;
} CdcUnionDescriptor;

typedef struct __attribute__((packed)) {
    u8 bLength;
    u8 bDescriptorType;
    u8 bDescriptorSubType;
    u8 iMACAddress;
    u32 bmEthernetStatistics;
    u16 wMaxSegmentSize;
    u16 wNumberMCFilters;
    u8 bNumberPowerFilters;
} CdcEthernetDescriptor;

typedef struct __attribute__((packed)) {
    u8 bLength;
    u8 bDescriptorType;
    u8 bDescriptorSubType;
    u16 bcdNcmVersion;
    u8 bmNetworkCapabilities;
} CdcNcmDescriptor;

typedef struct __attribute__((packed)) {
    CdcHeaderDescriptor header;
    CdcUnionDescriptor uni;
    CdcEthernetDescriptor ethernet;
    CdcNcmDescriptor ncm;
} NcmClassDescriptors;

typedef struct __attribute__((packed)) {
    u16 wLength;
    u16 bmNtbFormatsSupported;
    u32 dwNtbInMaxSize;
    u16 wNdpInDivisor;
    u16 wNdpInPayloadRemainder;
    u16 wNdpInAlignment;
    u16 wReserved;
    u32 dwNtbOutMaxSize;
    u16 wNdpOutDivisor;
    u16 wNdpOutPayloadRemainder;
    u16 wNdpOutAlignment;
    u16 wNtbOutMaxDatagrams;
} NtbParameters;

typedef struct __attribute__((packed)) {
    u32 dwNtbInMaxSize;
    u16 wNtbInMaxDatagrams;
    u16 wReserved;
} NtbInputSize;

typedef struct __attribute__((packed)) {
    u32 signature;
    u16 headerLength;
    u16 sequence;
    u16 blockLength;
    u16 ndpIndex;
} Nth16;

typedef struct __attribute__((packed)) {
    u32 signature;
    u16 length;
    u16 nextNdpIndex;
} Ndp16;

typedef struct __attribute__((packed)) {
    u16 datagramIndex;
    u16 datagramLength;
} Ndp16Entry;

typedef struct {
    bool initialized;
    bool configured;
    bool outPending;
    bool notificationsSent;
    u8 dataAlt;
    u16 packetFilter;
    u32 ntbInputSize;
    u16 ntbInputMaxDatagrams;
    u32 outUrbId;
    UsbDsInterface* controlInterface;
    UsbDsInterface* dataInterface;
    UsbDsEndpoint* notifyEndpointIn;
    UsbDsEndpoint* dataEndpointIn;
    UsbDsEndpoint* dataEndpointOut;
    UsbState lastState;
    bool haveState;
} NcmContext;

static NcmContext g_ctx;

static u8 alignas(0x1000) g_ctrlBuffer[0x1000];
static u8 alignas(0x1000) g_notifyBuffer[0x1000];
static u8 alignas(0x1000) g_dataOutBuffer[NCM_NTB_MAX_SIZE];

static IadDescriptor g_iad = {
    .bLength = 8,
    .bDescriptorType = USB_DT_INTERFACE_ASSOCIATION,
    .bFirstInterface = 0,
    .bInterfaceCount = 2,
    .bFunctionClass = USB_CLASS_CDC,
    .bFunctionSubClass = CDC_SUBCLASS_NCM,
    .bFunctionProtocol = 0,
    .iFunction = 0,
};

static struct usb_interface_descriptor g_controlInterfaceDesc = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 0,
    .bAlternateSetting = 0,
    .bNumEndpoints = 1,
    .bInterfaceClass = USB_CLASS_CDC,
    .bInterfaceSubClass = CDC_SUBCLASS_NCM,
    .bInterfaceProtocol = 0,
    .iInterface = 0,
};

static NcmClassDescriptors g_classDesc = {
    .header = {
        .bLength = sizeof(CdcHeaderDescriptor),
        .bDescriptorType = USB_CS_INTERFACE,
        .bDescriptorSubType = CDC_FUNC_HEADER,
        .bcdCDC = 0x0110,
    },
    .uni = {
        .bLength = sizeof(CdcUnionDescriptor),
        .bDescriptorType = USB_CS_INTERFACE,
        .bDescriptorSubType = CDC_FUNC_UNION,
        .bMasterInterface = 0,
        .bSlaveInterface = 1,
    },
    .ethernet = {
        .bLength = sizeof(CdcEthernetDescriptor),
        .bDescriptorType = USB_CS_INTERFACE,
        .bDescriptorSubType = CDC_FUNC_ETHERNET,
        .iMACAddress = 0,
        .bmEthernetStatistics = 0,
        .wMaxSegmentSize = NCM_MTU,
        .wNumberMCFilters = 0,
        .bNumberPowerFilters = 0,
    },
    .ncm = {
        .bLength = sizeof(CdcNcmDescriptor),
        .bDescriptorType = USB_CS_INTERFACE,
        .bDescriptorSubType = CDC_FUNC_NCM,
        .bcdNcmVersion = 0x0100,
        .bmNetworkCapabilities = NCM_CAPABILITIES,
    },
};

static struct usb_interface_descriptor g_dataInterfaceDesc0 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    .bNumEndpoints = 0,
    .bInterfaceClass = USB_CLASS_CDC_DATA,
    .bInterfaceSubClass = 0,
    .bInterfaceProtocol = NCM_DATA_PROTOCOL_NTB,
    .iInterface = 0,
};

static struct usb_interface_descriptor g_dataInterfaceDesc1 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 1,
    .bNumEndpoints = 2,
    .bInterfaceClass = USB_CLASS_CDC_DATA,
    .bInterfaceSubClass = 0,
    .bInterfaceProtocol = NCM_DATA_PROTOCOL_NTB,
    .iInterface = 0,
};

static struct usb_endpoint_descriptor g_notifyEndpointDesc = {
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x81,
    .bmAttributes = 0x03,
    .wMaxPacketSize = 64,
    .bInterval = 9,
};

static struct usb_endpoint_descriptor g_dataInEndpointDesc = {
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x82,
    .bmAttributes = USB_TRANSFER_TYPE_BULK,
    .wMaxPacketSize = 512,
    .bInterval = 0,
};

static struct usb_endpoint_descriptor g_dataOutEndpointDesc = {
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x02,
    .bmAttributes = USB_TRANSFER_TYPE_BULK,
    .wMaxPacketSize = 512,
    .bInterval = 0,
};

static struct usb_ss_endpoint_companion_descriptor g_ssBulkCompanion = {
    .bLength = sizeof(struct usb_ss_endpoint_companion_descriptor),
    .bDescriptorType = USB_DT_SS_ENDPOINT_COMPANION,
    .bMaxBurst = 0,
    .bmAttributes = 0,
    .wBytesPerInterval = 0,
};

static struct usb_ss_endpoint_companion_descriptor g_ssNotifyCompanion = {
    .bLength = sizeof(struct usb_ss_endpoint_companion_descriptor),
    .bDescriptorType = USB_DT_SS_ENDPOINT_COMPANION,
    .bMaxBurst = 0,
    .bmAttributes = 0,
    .wBytesPerInterval = 64,
};

static const NtbParameters g_ntbParameters = {
    .wLength = sizeof(NtbParameters),
    .bmNtbFormatsSupported = 0x0001,
    .dwNtbInMaxSize = NCM_NTB_MAX_SIZE,
    .wNdpInDivisor = 1,
    .wNdpInPayloadRemainder = 0,
    .wNdpInAlignment = 4,
    .wReserved = 0,
    .dwNtbOutMaxSize = NCM_NTB_MAX_SIZE,
    .wNdpOutDivisor = 1,
    .wNdpOutPayloadRemainder = 0,
    .wNdpOutAlignment = 4,
    .wNtbOutMaxDatagrams = NCM_MAX_DATAGRAMS,
};

static const char* _stateName(UsbState state)
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

static Result _append(UsbDsInterface* iface, UsbDeviceSpeed speed,
                      const char* name, const void* data, size_t size, u32* cumulative)
{
    Result rc = usbDsInterface_AppendConfigurationData(iface, speed, data, size);
    if (R_SUCCEEDED(rc)) *cumulative += (u32)size;
    LOG("A7-P0 append %s speed=%u size=%u cumulative=%u -> rc=0x%x\\n",
        name, (unsigned)speed, (unsigned)size, (unsigned)*cumulative, rc);
    return rc;
}

static Result _setupDescriptors(const NcmDeviceConfig* config)
{
    Result rc;
    u8 iManufacturer = 0, iProduct = 0, iSerial = 0, iMac = 0;
    static const u16 langs[] = { 0x0409 };

    rc = usbDsAddUsbLanguageStringDescriptor(NULL, langs, 1);
    if (R_FAILED(rc)) return rc;
    rc = usbDsAddUsbStringDescriptor(&iManufacturer, config->manufacturer);
    if (R_FAILED(rc)) return rc;
    rc = usbDsAddUsbStringDescriptor(&iProduct, config->product);
    if (R_FAILED(rc)) return rc;
    rc = usbDsAddUsbStringDescriptor(&iSerial, config->serialNumber);
    if (R_FAILED(rc)) return rc;
    rc = usbDsAddUsbStringDescriptor(&iMac, "020000000001");
    if (R_FAILED(rc)) return rc;
    g_classDesc.ethernet.iMACAddress = iMac;

    struct usb_device_descriptor deviceDesc = {
        .bLength = USB_DT_DEVICE_SIZE,
        .bDescriptorType = USB_DT_DEVICE,
        .bcdUSB = 0x0201,
        .bDeviceClass = USB_CLASS_MISC,
        .bDeviceSubClass = 0x02,
        .bDeviceProtocol = 0x01,
        .bMaxPacketSize0 = 64,
        .idVendor = config->vendorId,
        .idProduct = config->productId,
        .bcdDevice = 0x0100,
        .iManufacturer = iManufacturer,
        .iProduct = iProduct,
        .iSerialNumber = iSerial,
        .bNumConfigurations = 1,
    };

    rc = usbDsSetUsbDeviceDescriptor(UsbDeviceSpeed_High, &deviceDesc);
    if (R_FAILED(rc)) return rc;

    deviceDesc.bcdUSB = 0x0300;
    deviceDesc.bMaxPacketSize0 = 9;
    rc = usbDsSetUsbDeviceDescriptor(UsbDeviceSpeed_Super, &deviceDesc);
    if (R_FAILED(rc)) return rc;

    static const u8 bosDescriptor[] = {
        0x05, USB_DT_BOS, 0x16, 0x00, 0x02,
        0x07, USB_DT_DEVICE_CAPABILITY, 0x02, 0x02, 0x00, 0x00, 0x00,
        0x0A, USB_DT_DEVICE_CAPABILITY, 0x03, 0x00, 0x0E, 0x00,
        0x03, 0x00, 0x00, 0x00
    };
    return usbDsSetBinaryObjectStore(bosDescriptor, sizeof(bosDescriptor));
}

static Result _setupInterfaces(void)
{
    Result rc;
    rc = usbDsRegisterInterface(&g_ctx.controlInterface);
    if (R_FAILED(rc)) return rc;
    rc = usbDsRegisterInterface(&g_ctx.dataInterface);
    if (R_FAILED(rc)) return rc;

    const u8 ctrl = g_ctx.controlInterface->interface_index;
    const u8 data = g_ctx.dataInterface->interface_index;
    LOG("A7-P0 NCM interfaces: control=%u data=%u\\n", ctrl, data);

    g_iad.bFirstInterface = ctrl;
    g_controlInterfaceDesc.bInterfaceNumber = ctrl;
    g_classDesc.uni.bMasterInterface = ctrl;
    g_classDesc.uni.bSlaveInterface = data;
    g_dataInterfaceDesc0.bInterfaceNumber = data;
    g_dataInterfaceDesc1.bInterfaceNumber = data;

    g_notifyEndpointDesc.bEndpointAddress = USB_ENDPOINT_IN | (ctrl + 1);
    g_dataInEndpointDesc.bEndpointAddress = USB_ENDPOINT_IN | (data + 1);
    g_dataOutEndpointDesc.bEndpointAddress = (data + 1);

    u32 cum = 0;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_High, "HS IAD", &g_iad, sizeof(g_iad), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_High, "HS control-if", &g_controlInterfaceDesc, sizeof(g_controlInterfaceDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_High, "HS NCM-class", &g_classDesc, sizeof(g_classDesc), &cum);
    if (R_FAILED(rc)) return rc;
    g_notifyEndpointDesc.wMaxPacketSize = 64;
    g_notifyEndpointDesc.bInterval = 9;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_High, "HS notify-ep", &g_notifyEndpointDesc, sizeof(g_notifyEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;

    cum = 0;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_Super, "SS IAD", &g_iad, sizeof(g_iad), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_Super, "SS control-if", &g_controlInterfaceDesc, sizeof(g_controlInterfaceDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_Super, "SS NCM-class", &g_classDesc, sizeof(g_classDesc), &cum);
    if (R_FAILED(rc)) return rc;
    g_notifyEndpointDesc.wMaxPacketSize = 64;
    g_notifyEndpointDesc.bInterval = 9;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_Super, "SS notify-ep", &g_notifyEndpointDesc, sizeof(g_notifyEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.controlInterface, UsbDeviceSpeed_Super, "SS notify-comp", &g_ssNotifyCompanion, sizeof(g_ssNotifyCompanion), &cum);
    if (R_FAILED(rc)) return rc;

    rc = usbDsInterface_RegisterEndpoint(g_ctx.controlInterface,
        &g_ctx.notifyEndpointIn, g_notifyEndpointDesc.bEndpointAddress);
    if (R_FAILED(rc)) return rc;
    rc = usbDsInterface_EnableInterface(g_ctx.controlInterface);
    if (R_FAILED(rc)) return rc;

    cum = 0;
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

    rc = usbDsInterface_RegisterEndpoint(g_ctx.dataInterface,
        &g_ctx.dataEndpointIn, g_dataInEndpointDesc.bEndpointAddress);
    if (R_FAILED(rc)) return rc;
    rc = usbDsInterface_RegisterEndpoint(g_ctx.dataInterface,
        &g_ctx.dataEndpointOut, g_dataOutEndpointDesc.bEndpointAddress);
    if (R_FAILED(rc)) return rc;
    rc = usbDsInterface_EnableInterface(g_ctx.dataInterface);
    if (R_FAILED(rc)) return rc;

    LOG("A7-P0 NCM interfaces enabled; ctrlCumHS=53 dataCumHS=32\\n");
    return 0;
}

static Result _ctrlIn(UsbDsInterface* iface, void* data, size_t len)
{
    if (len > sizeof(g_ctrlBuffer)) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    if (len && data != g_ctrlBuffer) memcpy(g_ctrlBuffer, data, len);
    eventClear(&iface->CtrlInCompletionEvent);
    u32 urbId = 0;
    Result rc = usbDsInterface_CtrlInPostBufferAsync(iface, g_ctrlBuffer, len, &urbId);
    if (R_FAILED(rc)) return rc;
    rc = eventWait(&iface->CtrlInCompletionEvent, EP0_TIMEOUT_NS);
    if (R_FAILED(rc)) return rc;
    eventClear(&iface->CtrlInCompletionEvent);
    UsbDsReportData rpt;
    rc = usbDsInterface_GetCtrlInReportData(iface, &rpt);
    if (R_FAILED(rc)) return rc;
    return usbDsParseReportData(&rpt, urbId, NULL, NULL);
}

static Result _ctrlOut(UsbDsInterface* iface, void* data, size_t len, u32* transferred)
{
    if (len > sizeof(g_ctrlBuffer)) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    eventClear(&iface->CtrlOutCompletionEvent);
    u32 urbId = 0;
    Result rc = usbDsInterface_CtrlOutPostBufferAsync(iface, g_ctrlBuffer, len, &urbId);
    if (R_FAILED(rc)) return rc;
    rc = eventWait(&iface->CtrlOutCompletionEvent, EP0_TIMEOUT_NS);
    if (R_FAILED(rc)) return rc;
    eventClear(&iface->CtrlOutCompletionEvent);
    UsbDsReportData rpt;
    rc = usbDsInterface_GetCtrlOutReportData(iface, &rpt);
    if (R_FAILED(rc)) return rc;
    u32 got = 0;
    rc = usbDsParseReportData(&rpt, urbId, NULL, &got);
    if (R_SUCCEEDED(rc) && len && data) memcpy(data, g_ctrlBuffer, got < len ? got : len);
    if (transferred) *transferred = got;
    return rc;
}

static void _stall(UsbDsInterface* iface, const char* why)
{
    LOG("A7-P0 EP0 stall: %s\\n", why);
    usbDsInterface_StallCtrl(iface);
}

static bool _writeControl(UsbDsInterface* iface, const void* data, size_t len)
{
    Result rc = _ctrlIn(iface, (void*)data, len);
    if (R_SUCCEEDED(rc)) rc = _ctrlOut(iface, NULL, 0, NULL);
    if (R_FAILED(rc)) {
        LOG("A7-P0 control write failed: 0x%x\\n", rc);
        usbDsInterface_StallCtrl(iface);
        return false;
    }
    return true;
}

static bool _ackControl(UsbDsInterface* iface)
{
    Result rc = _ctrlIn(iface, NULL, 0);
    if (R_FAILED(rc)) {
        LOG("A7-P0 control ACK failed: 0x%x\\n", rc);
        usbDsInterface_StallCtrl(iface);
        return false;
    }
    return true;
}

static bool _readControl(UsbDsInterface* iface, void* data, size_t len)
{
    Result rc = _ctrlOut(iface, data, len, NULL);
    if (R_SUCCEEDED(rc)) rc = _ctrlIn(iface, NULL, 0);
    if (R_FAILED(rc)) {
        LOG("A7-P0 control read failed: 0x%x\\n", rc);
        usbDsInterface_StallCtrl(iface);
        return false;
    }
    return true;
}

static void _sendEndpointSync(UsbDsEndpoint* ep, const void* data, size_t len, const char* label)
{
    if (!ep || len > sizeof(g_notifyBuffer)) return;
    memcpy(g_notifyBuffer, data, len);
    eventClear(&ep->CompletionEvent);
    u32 urbId = 0;
    Result rc = usbDsEndpoint_PostBufferAsync(ep, g_notifyBuffer, len, &urbId);
    if (R_FAILED(rc)) {
        LOG("A7-P0 %s post failed: 0x%x\\n", label, rc);
        return;
    }
    rc = eventWait(&ep->CompletionEvent, EP_TIMEOUT_NS);
    if (R_FAILED(rc)) {
        usbDsEndpoint_Cancel(ep);
        if (R_SUCCEEDED(eventWait(&ep->CompletionEvent, 100000000ULL))) {
            eventClear(&ep->CompletionEvent);
            UsbDsReportData rpt;
            usbDsEndpoint_GetReportData(ep, &rpt);
        }
        LOG("A7-P0 %s timeout\\n", label);
        return;
    }
    eventClear(&ep->CompletionEvent);
    UsbDsReportData rpt;
    rc = usbDsEndpoint_GetReportData(ep, &rpt);
    if (R_SUCCEEDED(rc)) {
        u32 transferred = 0;
        rc = usbDsParseReportData(&rpt, urbId, NULL, &transferred);
        LOG("A7-P0 %s complete rc=0x%x bytes=%u\\n", label, rc, transferred);
    }
}

static void _sendLinkNotifications(void)
{
    if (g_ctx.notificationsSent || g_ctx.dataAlt != 1 || !g_ctx.configured) return;

    struct __attribute__((packed)) {
        UsbSetupPacket h;
        u32 downlink;
        u32 uplink;
    } speed = {
        .h = { .bmRequestType = 0xA1, .bRequest = CDC_NOTIF_CONNECTION_SPEED_CHANGE,
               .wValue = 0, .wIndex = g_ctx.controlInterface->interface_index, .wLength = 8 },
        .downlink = 480000000u,
        .uplink = 480000000u,
    };
    UsbSetupPacket connected = {
        .bmRequestType = 0xA1,
        .bRequest = CDC_NOTIF_NETWORK_CONNECTION,
        .wValue = 1,
        .wIndex = g_ctx.controlInterface->interface_index,
        .wLength = 0,
    };

    LOG("A7-P0 sending NCM link notifications\\n");
    _sendEndpointSync(g_ctx.notifyEndpointIn, &speed, sizeof(speed), "speed-notify");
    _sendEndpointSync(g_ctx.notifyEndpointIn, &connected, sizeof(connected), "link-up-notify");
    g_ctx.notificationsSent = true;
}

static void _cancelOut(void)
{
    if (!g_ctx.outPending || !g_ctx.dataEndpointOut) return;
    usbDsEndpoint_Cancel(g_ctx.dataEndpointOut);
    if (R_SUCCEEDED(eventWait(&g_ctx.dataEndpointOut->CompletionEvent, 100000000ULL))) {
        eventClear(&g_ctx.dataEndpointOut->CompletionEvent);
        UsbDsReportData rpt;
        usbDsEndpoint_GetReportData(g_ctx.dataEndpointOut, &rpt);
    }
    g_ctx.outPending = false;
}

static void _postOut(void)
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

static u16 _be16(const u8* p)
{
    return ((u16)p[0] << 8) | p[1];
}

static void _logNcmRx(const u8* p, u32 len)
{
    if (len < sizeof(Nth16)) {
        LOG("A7-P0 NCM RX short: %u bytes\\n", len);
        return;
    }
    const Nth16* nth = (const Nth16*)p;
    LOG("A7-P0 NCM RX: bytes=%u sig=0x%08x hdr=%u seq=%u block=%u ndp=%u\\n",
        len, nth->signature, nth->headerLength, nth->sequence, nth->blockLength, nth->ndpIndex);

    if (nth->signature != NTH16_SIGNATURE || nth->ndpIndex + sizeof(Ndp16) + sizeof(Ndp16Entry) > len)
        return;
    const Ndp16* ndp = (const Ndp16*)(p + nth->ndpIndex);
    if (ndp->signature != NDP16_SIGNATURE_0 && ndp->signature != NDP16_SIGNATURE_1)
        return;
    const Ndp16Entry* e = (const Ndp16Entry*)((const u8*)ndp + sizeof(Ndp16));
    if (!e->datagramIndex || e->datagramLength < 14 ||
        (u32)e->datagramIndex + e->datagramLength > len)
        return;
    const u8* eth = p + e->datagramIndex;
    const u16 etherType = _be16(eth + 12);
    LOG("A7-P0 Ethernet frame: off=%u len=%u type=0x%04x src=%02x:%02x:%02x:%02x:%02x:%02x dst=%02x:%02x:%02x:%02x:%02x:%02x\\n",
        e->datagramIndex, e->datagramLength, etherType,
        eth[6], eth[7], eth[8], eth[9], eth[10], eth[11],
        eth[0], eth[1], eth[2], eth[3], eth[4], eth[5]);
    if (etherType == 0x0800 && e->datagramLength >= 14 + 20) {
        const u8* ip = eth + 14;
        const u8 ihl = (ip[0] & 0x0F) * 4;
        if (ip[9] == 17 && ihl >= 20 && e->datagramLength >= 14 + ihl + 8) {
            const u8* udp = ip + ihl;
            LOG("A7-P0 IPv4 UDP: %u -> %u\\n", _be16(udp), _be16(udp + 2));
        }
    }
}

static void _pollOut(void)
{
    if (!g_ctx.outPending || !g_ctx.dataEndpointOut) return;
    if (R_FAILED(eventWait(&g_ctx.dataEndpointOut->CompletionEvent, 0))) return;
    eventClear(&g_ctx.dataEndpointOut->CompletionEvent);
    UsbDsReportData rpt;
    Result rc = usbDsEndpoint_GetReportData(g_ctx.dataEndpointOut, &rpt);
    u32 transferred = 0;
    if (R_SUCCEEDED(rc)) rc = usbDsParseReportData(&rpt, g_ctx.outUrbId, NULL, &transferred);
    g_ctx.outPending = false;
    if (R_SUCCEEDED(rc)) _logNcmRx(g_dataOutBuffer, transferred);
    else LOG("A7-P0 NCM OUT completion failed: 0x%x\\n", rc);
    _postOut();
}

static void _handleClassRequest(UsbDsInterface* iface, const UsbSetupPacket* setup)
{
    if (iface != g_ctx.controlInterface) {
        _stall(iface, "class request on data interface");
        return;
    }

    switch (setup->bRequest) {
        case NCM_GET_NTB_PARAMETERS: {
            size_t len = setup->wLength < sizeof(g_ntbParameters) ? setup->wLength : sizeof(g_ntbParameters);
            LOG("A7-P0 GET_NTB_PARAMETERS len=%u\\n", setup->wLength);
            _writeControl(iface, &g_ntbParameters, len);
            break;
        }
        case NCM_SET_ETHERNET_PACKET_FILTER:
            g_ctx.packetFilter = setup->wValue;
            LOG("A7-P0 SET_ETHERNET_PACKET_FILTER=0x%04x\\n", g_ctx.packetFilter);
            _ackControl(iface);
            break;
        case NCM_GET_NTB_FORMAT: {
            u16 fmt = 0;
            LOG("A7-P0 GET_NTB_FORMAT\\n");
            _writeControl(iface, &fmt, setup->wLength < 2 ? setup->wLength : 2);
            break;
        }
        case NCM_SET_NTB_FORMAT:
            LOG("A7-P0 SET_NTB_FORMAT=%u\\n", setup->wValue);
            if (setup->wValue == 0) _ackControl(iface);
            else _stall(iface, "only NTB16 supported");
            break;
        case NCM_GET_NTB_INPUT_SIZE: {
            NtbInputSize v = {
                .dwNtbInMaxSize = g_ctx.ntbInputSize,
                .wNtbInMaxDatagrams = g_ctx.ntbInputMaxDatagrams,
                .wReserved = 0,
            };
            size_t len = setup->wLength >= 8 ? 8 : setup->wLength;
            if (len < 4) {
                _stall(iface, "GET_NTB_INPUT_SIZE len < 4");
                break;
            }
            LOG("A7-P0 GET_NTB_INPUT_SIZE len=%u value=%u\\n", setup->wLength, g_ctx.ntbInputSize);
            _writeControl(iface, &v, len);
            break;
        }
        case NCM_SET_NTB_INPUT_SIZE: {
            if (setup->wLength != 4 && setup->wLength != 8) {
                _stall(iface, "SET_NTB_INPUT_SIZE len");
                break;
            }
            NtbInputSize v;
            memset(&v, 0, sizeof(v));
            if (!_readControl(iface, &v, setup->wLength)) break;
            if (v.dwNtbInMaxSize >= 2048 && v.dwNtbInMaxSize <= 65535) {
                g_ctx.ntbInputSize = v.dwNtbInMaxSize < NCM_NTB_MAX_SIZE ? v.dwNtbInMaxSize : NCM_NTB_MAX_SIZE;
                g_ctx.ntbInputMaxDatagrams = (setup->wLength == 8 && v.wNtbInMaxDatagrams) ? v.wNtbInMaxDatagrams : NCM_MAX_DATAGRAMS;
                if (g_ctx.ntbInputMaxDatagrams > NCM_MAX_DATAGRAMS) g_ctx.ntbInputMaxDatagrams = NCM_MAX_DATAGRAMS;
            }
            LOG("A7-P0 SET_NTB_INPUT_SIZE host=%u datagrams=%u -> device=%u/%u\\n",
                v.dwNtbInMaxSize, v.wNtbInMaxDatagrams, g_ctx.ntbInputSize, g_ctx.ntbInputMaxDatagrams);
            break;
        }
        default:
            LOG("A7-P0 unhandled NCM class request 0x%02x\\n", setup->bRequest);
            _stall(iface, "unsupported NCM class request");
            break;
    }
}

static void _handleSetup(UsbDsInterface* iface)
{
    UsbSetupPacket setup;
    Result rc = usbDsInterface_GetSetupPacket(iface, &setup, sizeof(setup));
    if (R_FAILED(rc)) {
        LOG("A7-P0 GetSetupPacket failed: 0x%x\\n", rc);
        return;
    }
    LOG("A7-P0 EP0[%u]: type=0x%02x req=0x%02x wVal=0x%04x wIdx=0x%04x wLen=%u\\n",
        iface->interface_index, setup.bmRequestType, setup.bRequest,
        setup.wValue, setup.wIndex, setup.wLength);

    const u8 type = setup.bmRequestType & 0x60;
    const u8 recipient = setup.bmRequestType & 0x1F;
    if (recipient == 0x01 && (setup.wIndex & 0xFF) != iface->interface_index) {
        LOG("A7-P0 EP0[%u]: wIndex mismatch, ignoring\\n", iface->interface_index);
        return;
    }

    if (type == 0x20 && recipient == 0x01) {
        _handleClassRequest(iface, &setup);
        return;
    }

    if (type == 0x00 && recipient == 0x01) {
        if (setup.bRequest == USB_REQUEST_GET_INTERFACE && (setup.bmRequestType & 0x80)) {
            u8 alt = (iface == g_ctx.dataInterface) ? g_ctx.dataAlt : 0;
            LOG("A7-P0 GET_INTERFACE[%u] -> %u\\n", iface->interface_index, alt);
            _writeControl(iface, &alt, 1);
            return;
        }
        if (setup.bRequest == USB_REQUEST_SET_INTERFACE) {
            if (iface == g_ctx.dataInterface && setup.wValue <= 1) {
                g_ctx.dataAlt = (u8)setup.wValue;
                g_ctx.notificationsSent = false;
                LOG("A7-P0 SET_INTERFACE data alt=%u\\n", g_ctx.dataAlt);
                _ackControl(iface);
                if (g_ctx.dataAlt == 0) _cancelOut();
                else {
                    _sendLinkNotifications();
                    _postOut();
                }
                return;
            }
            if (iface == g_ctx.controlInterface && setup.wValue == 0) {
                _ackControl(iface);
                return;
            }
            _stall(iface, "invalid alternate setting");
            return;
        }
    }

    _stall(iface, "unhandled setup request");
}

static bool _serviceSetup(UsbDsInterface* iface)
{
    if (!iface || R_FAILED(eventWait(&iface->SetupEvent, 0))) return false;
    eventClear(&iface->SetupEvent);
    _handleSetup(iface);
    return true;
}

Result NcmDeviceInitialize(const NcmDeviceConfig* config)
{
    if (g_ctx.initialized) return MAKERESULT(Module_Libnx, LibnxError_AlreadyInitialized);
    memset(&g_ctx, 0, sizeof(g_ctx));
    g_ctx.ntbInputSize = NCM_NTB_MAX_SIZE;
    g_ctx.ntbInputMaxDatagrams = NCM_MAX_DATAGRAMS;

    Result rc = usbDsInitialize();
    if (R_FAILED(rc)) {
        LOG("A7-P0 usbDsInitialize failed: 0x%x\\n", rc);
        return rc;
    }
    rc = _setupDescriptors(config);
    if (R_FAILED(rc)) {
        LOG("A7-P0 descriptor setup failed: 0x%x\\n", rc);
        goto fail;
    }
    rc = _setupInterfaces();
    if (R_FAILED(rc)) {
        LOG("A7-P0 interface setup failed: 0x%x\\n", rc);
        goto fail;
    }
    rc = usbDsEnable();
    if (R_FAILED(rc)) {
        LOG("A7-P0 usbDsEnable failed: 0x%x\\n", rc);
        goto fail;
    }

    g_ctx.initialized = true;
    LOG("A7-P0 USB NCM initialized: MAC=02:00:00:00:00:01 MTU=%u NTB=%u\\n", NCM_MTU, NCM_NTB_MAX_SIZE);
    return 0;

fail:
    if (g_ctx.dataInterface) usbDsInterface_Close(g_ctx.dataInterface);
    if (g_ctx.controlInterface) usbDsInterface_Close(g_ctx.controlInterface);
    usbDsExit();
    memset(&g_ctx, 0, sizeof(g_ctx));
    return rc;
}

void NcmDeviceProcessRequests(void)
{
    if (!g_ctx.initialized) return;
    UsbState state = UsbState_Detached;
    if (R_FAILED(usbDsGetState(&state))) return;

    if (!g_ctx.haveState || state != g_ctx.lastState) {
        UsbDeviceSpeed speed = UsbDeviceSpeed_None;
        Result src = usbDsGetSpeed(&speed);
        LOG("A7-P0 USB state: %u (%s), speed=%u speedRc=0x%x\\n",
            (unsigned)state, _stateName(state), (unsigned)speed, src);
        g_ctx.lastState = state;
        g_ctx.haveState = true;
    }

    const bool nowConfigured = state == UsbState_Configured;
    if (nowConfigured != g_ctx.configured) {
        g_ctx.configured = nowConfigured;
        if (nowConfigured) {
            LOG("A7-P0 USB host configured\\n");
        } else {
            LOG("A7-P0 USB host disconnected/unconfigured\\n");
            g_ctx.dataAlt = 0;
            g_ctx.notificationsSent = false;
            _cancelOut();
        }
    }
    if (!nowConfigured) return;

    bool serviced = _serviceSetup(g_ctx.controlInterface);
    serviced |= _serviceSetup(g_ctx.dataInterface);
    while (serviced) {
        s32 idx;
        if (R_FAILED(waitMulti(&idx, 20000000ULL,
            waiterForEvent(&g_ctx.controlInterface->SetupEvent),
            waiterForEvent(&g_ctx.dataInterface->SetupEvent)))) break;
        serviced = _serviceSetup(g_ctx.controlInterface);
        serviced |= _serviceSetup(g_ctx.dataInterface);
    }

    _sendLinkNotifications();
    _postOut();
    _pollOut();
}

void NcmDeviceExit(void)
{
    if (!g_ctx.initialized) return;
    _cancelOut();
    if (g_ctx.dataEndpointIn) usbDsEndpoint_Cancel(g_ctx.dataEndpointIn);
    if (g_ctx.notifyEndpointIn) usbDsEndpoint_Cancel(g_ctx.notifyEndpointIn);
    if (g_ctx.dataInterface) usbDsInterface_Close(g_ctx.dataInterface);
    if (g_ctx.controlInterface) usbDsInterface_Close(g_ctx.controlInterface);
    usbDsExit();
    memset(&g_ctx, 0, sizeof(g_ctx));
    LOG("A7-P0 USB NCM exited\\n");
}
''')

# -----------------------------------------------------------------------------
# Replace sysmodule main with NCM diagnostic lifecycle. No capture threads.
# -----------------------------------------------------------------------------
(root / 'source/sysmodule/main.c').write_text(r'''#include <switch.h>
#include "../core.h"
#include "../usbnet/ncm_device.h"

u32 __nx_applet_type = AppletType_None;
u32 __nx_fs_num_sessions = 1;
u32 __nx_fsdev_direntry_cache_size = 1;

#define INNER_HEAP_SIZE (128 * 1024)
static char nx_inner_heap[INNER_HEAP_SIZE];
static volatile bool g_running = true;

void __libnx_initheap(void)
{
    extern char* fake_heap_start;
    extern char* fake_heap_end;
    fake_heap_start = nx_inner_heap;
    fake_heap_end = nx_inner_heap + INNER_HEAP_SIZE;
}

void __appInit(void)
{
    Result rc;
    svcSleepThread(20E+9);
    rc = smInitialize();
    if (R_FAILED(rc)) fatalThrow(rc);

    rc = setsysInitialize();
    if (R_SUCCEEDED(rc)) {
        SetSysFirmwareVersion fw;
        if (R_SUCCEEDED(setsysGetFirmwareVersion(&fw)))
            hosversionSet(MAKEHOSVERSION(fw.major, fw.minor, fw.micro));
        setsysExit();
    }

    rc = CoreInit();
    if (R_FAILED(rc)) fatalThrow(rc);
}

void __appExit(void)
{
    g_running = false;
    NcmDeviceExit();
    CoreExit();
    smExit();
}

int main(int argc, char** argv)
{
    (void)argc;
    (void)argv;
    LOG("A7-P0 SysDVR USB-NCM binding diagnostic starting...\\n");

    NcmDeviceConfig cfg = {
        .vendorId = 0x057E,
        .productId = 0x3001,
        .manufacturer = "Nintendo Switch",
        .product = "SysDVR USB NCM",
        .serialNumber = CoreGetSerialNumber(),
    };

    Result rc = NcmDeviceInitialize(&cfg);
    if (R_FAILED(rc)) {
        LOG("A7-P0 NcmDeviceInitialize failed: 0x%x\\n", rc);
        return 1;
    }

    LOG("A7-P0 running. Connect iPad through the proven USB hub topology.\\n");
    while (g_running) {
        NcmDeviceProcessRequests();
        svcSleepThread(5000000ULL); // 5 ms
    }

    NcmDeviceExit();
    return 0;
}
''')

print('A7-P0 NCM diagnostic patch applied')
