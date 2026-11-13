/*
 * REMOTE - remote desktop client for the TI-84 Plus CE.
 *
 * The calculator plugs into the PC over USB and shows up as a USB serial
 * (CDC ACM) device. The host program streams the desktop as delta-encoded
 * 320x240 8bpp frames; this program draws them and reports the raw keypad
 * state, which the host turns into mouse and keyboard input.
 *
 * Quit with [2nd] + [mode].
 */
#include <graphx.h>
#include <keypadc.h>
#include <srldrvce.h>
#include <usbdrvce.h>

#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include "decode.h"
#include "proto.h"

#define FB_SIZE (GFX_LCD_WIDTH * GFX_LCD_HEIGHT)

#define RX_TIMEOUT   (CLOCKS_PER_SEC * 3 / 2) /* give up on the host after 1.5 s */
#define KEY_INTERVAL (CLOCKS_PER_SEC / 200)   /* scan the keypad every 5 ms while waiting */

#define COLOR_BLACK 0
#define COLOR_WHITE 255

static srl_device_t srl;
static bool has_srl;
static usb_device_t pending_dev; /* serial device waiting to be opened */

static uint8_t srl_buf[2048];
static uint8_t rx_buf[1024];
static uint8_t palette_buf[512];

static uint8_t keys[KEY_GROUPS];
static clock_t last_key_scan;
static bool quit;

static usb_error_t handle_usb_event(usb_event_t event, void *event_data,
                                    usb_callback_data_t *callback_data) {
    usb_error_t err;

    if ((err = srl_UsbEventCallback(event, event_data, callback_data)) != USB_SUCCESS)
        return err;

    if (event == USB_DEVICE_CONNECTED_EVENT && !(usb_GetRole() & USB_ROLE_DEVICE)) {
        /* The calculator is the USB host (e.g. a USB serial adapter was plugged in). */
        usb_ResetDevice(event_data);
    }

    if (event == USB_HOST_CONFIGURE_EVENT) {
        /* A PC configured us: the serial device is the host itself. */
        usb_device_t host = usb_FindDevice(NULL, NULL, USB_SKIP_HUBS);
        if (host && !has_srl) pending_dev = host;
    } else if (event == USB_DEVICE_ENABLED_EVENT && !(usb_GetRole() & USB_ROLE_DEVICE)) {
        if (!has_srl) pending_dev = event_data;
    }

    if (event == USB_DEVICE_DISCONNECTED_EVENT) {
        usb_device_t device = event_data;
        if (device == pending_dev) pending_dev = NULL;
        if (has_srl && device == srl.dev) {
            srl_Close(&srl);
            has_srl = false;
        }
    }

    return USB_SUCCESS;
}

/* srl_Open must not run inside the USB event handler, so it is deferred to here. */
static void try_open(void) {
    if (has_srl || !pending_dev) return;
    usb_device_t device = pending_dev;
    pending_dev = NULL;
    if (srl_Open(&srl, device, srl_buf, sizeof srl_buf, SRL_INTERFACE_ANY, 115200) == SRL_SUCCESS)
        has_srl = true;
}

static void tx(const uint8_t *data, unsigned int len) {
    clock_t start = clock();
    while (len && has_srl) {
        int n = srl_Write(&srl, data, len);
        if (n < 0) {
            has_srl = false;
            return;
        }
        data += n;
        len -= n;
        usb_HandleEvents();
        if (clock() - start > RX_TIMEOUT) return;
    }
}

static void tx_byte(uint8_t b) {
    tx(&b, 1);
}

/* Scan the keypad and report it to the host if anything changed. */
static void poll_keys(void) {
    uint8_t now[KEY_GROUPS];
    uint8_t i;

    kb_Scan();
    last_key_scan = clock();
    for (i = 0; i < KEY_GROUPS; i++) now[i] = kb_Data[i + 1];

    if ((now[0] & kb_2nd) && (now[0] & kb_Mode)) {
        quit = true;
        return;
    }
    if (memcmp(now, keys, KEY_GROUPS)) {
        uint8_t msg[1 + KEY_GROUPS];
        memcpy(keys, now, KEY_GROUPS);
        msg[0] = MSG_KEYS;
        memcpy(msg + 1, now, KEY_GROUPS);
        tx(msg, sizeof msg);
    }
}

/* Stream refill: block until the host sends something, or time out. */
static bool rx_more(rd_stream_t *s) {
    clock_t start = clock();
    for (;;) {
        int n;

        usb_HandleEvents();
        if (!has_srl) return false;

        n = srl_Read(&srl, rx_buf, sizeof rx_buf);
        if (n < 0) {
            has_srl = false;
            return false;
        }
        if (n > 0) {
            s->p = rx_buf;
            s->end = rx_buf + n;
            return true;
        }

        if (clock() - last_key_scan >= KEY_INTERVAL) {
            poll_keys();
            if (quit) return false;
        }
        if (clock() - start > RX_TIMEOUT) return false;
    }
}

/* Throw away anything the host sent, until it has been quiet for 100 ms. */
static void rx_drain(void) {
    clock_t quiet = clock();
    while (has_srl && clock() - quiet < CLOCKS_PER_SEC / 10) {
        usb_HandleEvents();
        if (!has_srl) break;
        if (srl_Read(&srl, rx_buf, sizeof rx_buf) > 0) quiet = clock();
    }
}

static void status(const char *line1, const char *line2) {
    gfx_palette[COLOR_BLACK] = gfx_RGBTo1555(0, 0, 0);
    gfx_palette[COLOR_WHITE] = gfx_RGBTo1555(255, 255, 255);
    gfx_FillScreen(COLOR_BLACK);
    gfx_SetTextFGColor(COLOR_WHITE);
    gfx_SetTextBGColor(COLOR_BLACK);
    gfx_SetTextTransparentColor(COLOR_BLACK);
    gfx_PrintStringXY("TI-84 Plus CE Remote Desktop", 8, 8);
    gfx_PrintStringXY(line1, 8, 40);
    gfx_PrintStringXY(line2, 8, 56);
    gfx_PrintStringXY("[2nd] + [mode] to quit", 8, 224);
}

/* One request/response exchange. Returns false if the host went quiet or out of sync. */
static bool exchange(bool full) {
    rd_stream_t s = {rx_buf, rx_buf, rx_more};

    if (full) {
        uint8_t hello[2] = {MSG_HELLO, PROTO_VERSION};
        tx(hello, sizeof hello);
    } else {
        tx_byte(MSG_REQ);
    }

    for (;;) {
        uint8_t type;
        if (!rd_read(&s, &type, 1)) return false;

        if (type == MSG_PALETTE) {
            if (!rd_read(&s, palette_buf, sizeof palette_buf)) return false;
            gfx_SetPalette(palette_buf, sizeof palette_buf, 0);
        } else if (type == MSG_FRAME) {
            return rd_decode_frame(&s, gfx_vram, FB_SIZE);
        } else {
            return false;
        }
    }
}

int main(void) {
    enum { ST_NONE, ST_NO_USB, ST_NO_HOST, ST_LIVE } shown = ST_NONE;
    bool need_full = true;

    gfx_Begin();

    if (usb_Init(handle_usb_event, NULL, srl_GetCDCStandardDescriptors(),
                 USB_DEFAULT_INIT_FLAGS) != USB_SUCCESS) {
        status("USB init failed.", "Press [clear] to exit.");
        do kb_Scan(); while (!(kb_Data[6] & kb_Clear));
        usb_Cleanup();
        gfx_End();
        return 1;
    }

    while (!quit) {
        usb_HandleEvents();
        try_open();

        if (!has_srl) {
            if (shown != ST_NO_USB) {
                status("Waiting for USB connection...",
                       "Plug the calculator into the PC.");
                shown = ST_NO_USB;
            }
            need_full = true;
            poll_keys();
            continue;
        }

        poll_keys();
        if (quit) break;

        if (exchange(need_full)) {
            need_full = false;
            shown = ST_LIVE;
        } else if (!quit) {
            /* Host missing, stalled, or out of sync: resynchronise from scratch. */
            if (has_srl && shown != ST_NO_HOST) {
                status("USB connected. Waiting for host...",
                       "Run the host program on the PC.");
                shown = ST_NO_HOST;
            }
            rx_drain();
            need_full = true;
        }
    }

    if (has_srl) {
        uint8_t i;
        tx_byte(MSG_BYE);
        for (i = 0; i < 100; i++) usb_HandleEvents(); /* let the goodbye go out */
    }

    usb_Cleanup();
    gfx_End();
    return 0;
}
