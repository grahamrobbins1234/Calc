/*
 * REMOTE - remote desktop client for the TI-84 Plus CE.
 *
 * The calculator plugs into the PC (or the Wi-Fi bridge) over USB and shows
 * up as a USB serial (CDC ACM) device. The host streams the desktop as
 * delta-encoded frames; this program draws them, draws the mouse pointer,
 * and reports the raw keypad state, which the host turns into mouse and
 * keyboard input.
 *
 * Quit with [2nd] + [mode].
 */
#include <graphx.h>
#include <keypadc.h>
#include <srldrvce.h>
#include <sys/lcd.h>
#include <usbdrvce.h>

#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include "decode.h"
#include "proto.h"

#define RX_TIMEOUT   (CLOCKS_PER_SEC * 3 / 2) /* give up on the host after 1.5 s of silence */
#define KEY_INTERVAL (CLOCKS_PER_SEC / 200)   /* scan the keypad every 5 ms while waiting */

#define COLOR_BLACK 0
#define COLOR_WHITE 255

/* LCD controller bits-per-pixel field (bits 1-3 of the control register). */
#define LCD_BPP_MASK 0x0E
#define LCD_BPP_1    0x00
#define LCD_BPP_4    0x04
#define LCD_BPP_8    0x06

/*
 * The framebuffer never needs more than the first half of video memory
 * (320x240 at 8 bits per pixel), so the second half holds the packet buffers.
 */
#define FB       ((uint8_t *)gfx_vram)
#define PKT_WIRE (FB + SCREEN_W * SCREEN_H)
#define PKT_PLAIN (PKT_WIRE + PKT_MAX)
#define HALF_FB  (PKT_PLAIN + PKT_MAX) /* the 160x120 picture in half-resolution mode */

static srl_device_t srl;
static bool has_srl;
static usb_device_t pending_dev; /* serial device waiting to be opened */

static uint8_t srl_buf[2048];
static uint8_t palette_buf[512];

static uint8_t bpp = 8;
static bool half_res;
static cursor_t cursor;
static unsigned int cursor_x, cursor_y = CURSOR_HIDDEN;

static uint8_t keys[KEY_GROUPS];
static clock_t last_key_scan;
static bool quit;
static bool picture; /* the host has drawn over the status screen */

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

/* Read exactly `len` bytes from the host. Fails after RX_TIMEOUT without progress. */
static bool rx_exact(uint8_t *dst, unsigned int len) {
    clock_t last = clock();
    while (len) {
        int n;

        /* Keys come first: scan on schedule even while data is pouring in. */
        if (clock() - last_key_scan >= KEY_INTERVAL) {
            poll_keys();
            if (quit) return false;
        }

        usb_HandleEvents();
        if (!has_srl) return false;

        n = srl_Read(&srl, dst, len);
        if (n < 0) {
            has_srl = false;
            return false;
        }
        if (n > 0) {
            dst += n;
            len -= n;
            last = clock();
        } else if (clock() - last > RX_TIMEOUT) {
            return false;
        }
    }
    return true;
}

/* Throw away anything the host sent, until it has been quiet for 100 ms. */
static void rx_drain(void) {
    clock_t quiet = clock();
    while (has_srl && clock() - quiet < CLOCKS_PER_SEC / 10) {
        usb_HandleEvents();
        if (!has_srl) break;
        if (srl_Read(&srl, PKT_PLAIN, PKT_MAX) > 0) quiet = clock();
    }
}

static bool set_mode(uint8_t new_bpp) {
    uint8_t code;

    if (new_bpp == 8) code = LCD_BPP_8;
    else if (new_bpp == 4) code = LCD_BPP_4;
    else if (new_bpp == 1) code = LCD_BPP_1;
    else return false;

    cursor.shown = false; /* whatever was saved under the pointer is meaningless now */
    bpp = new_bpp;
    lcd_Control = (lcd_Control & ~(uint24_t)LCD_BPP_MASK) | code;
    return true;
}

/* Shown on the waiting screen so a rejected frame can be reported. */
static uint8_t last_error;

static void status(const char *line1, const char *line2) {
    set_mode(8); /* graphx draws 8 bits per pixel */
    gfx_palette[COLOR_BLACK] = gfx_RGBTo1555(0, 0, 0);
    gfx_palette[COLOR_WHITE] = gfx_RGBTo1555(255, 255, 255);
    gfx_FillScreen(COLOR_BLACK);
    gfx_SetTextFGColor(COLOR_WHITE);
    gfx_SetTextBGColor(COLOR_BLACK);
    gfx_SetTextTransparentColor(COLOR_BLACK);
    gfx_PrintStringXY("TI-84 Plus CE Remote Desktop", 8, 8);
    gfx_PrintStringXY(line1, 8, 40);
    gfx_PrintStringXY(line2, 8, 56);
    if (last_error) {
        gfx_PrintStringXY("Last link error code:", 8, 88);
        gfx_SetTextXY(160, 88);
        gfx_PrintUInt(last_error, 1);
    }
    gfx_PrintStringXY("[2nd] + [mode] to quit", 8, 224);
}

static void pointer_hide(void) {
    cursor_hide(&cursor, FB);
}

static void pointer_show(void) {
    if (cursor_y != CURSOR_HIDDEN) cursor_show(&cursor, FB, bpp, cursor_x, cursor_y);
}

/* The host moved the pointer. Arrives out-of-band, possibly in the middle of a frame. */
static void pointer_moved(const uint8_t *note) {
    cursor_x = note[0] | ((unsigned int)note[1] << 8);
    cursor_y = note[2];
    pointer_hide();
    pointer_show();
    rd_retouch = true; /* lift it again before the frame draws anything more */
}

/* Handle host messages until one fails. Returns only on error, timeout or quit. */
static void serve(void) {
    rd_stream_t s;
    uint8_t hello[2] = {MSG_HELLO, PROTO_VERSION};

    pk_init(&s, rx_exact, pointer_moved, PKT_WIRE, PKT_PLAIN);
    cursor_y = CURSOR_HIDDEN;
    tx(hello, sizeof hello);

    for (;;) {
        uint8_t type, arg[1];
        unsigned int count;

        if (!rd_read(&s, &type, 1)) return;

        switch (type) {
        case MSG_MODE:
            if (!rd_read(&s, arg, 1)) return;
            if (!set_mode(arg[0] & (uint8_t)~MODE_HALF)) {
                rd_error = RD_ERR_MODE;
                return;
            }
            half_res = (arg[0] & MODE_HALF) != 0;
            picture = true;
            break;

        case MSG_PALETTE:
            if (!rd_read(&s, arg, 1)) return;
            count = arg[0] ? arg[0] : 256;
            if (!rd_read(&s, palette_buf, count * 2)) return;
            gfx_SetPalette(palette_buf, count * 2, 0);
            break;

        case MSG_FRAME:
            if (half_res) {
                /* Decode off-screen, then draw the changed rows doubled. */
                if (!rd_decode_frame(&s, HALF_FB, SCREEN_W * SCREEN_H / 4 * bpp / 8,
                                     SCREEN_W / 2 * bpp / 8, NULL))
                    return;
                if (rd_dirty_hi > rd_dirty_lo) {
                    pointer_hide();
                    rd_expand(FB, HALF_FB, bpp, rd_dirty_lo, rd_dirty_hi);
                }
            } else {
                /* The pointer is lifted off the picture only if the frame changes something. */
                if (!rd_decode_frame(&s, FB, SCREEN_W * SCREEN_H * bpp / 8,
                                     SCREEN_W * bpp / 8, pointer_hide))
                    return;
            }
            pointer_show();
            tx_byte(MSG_ACK);
            break;

        default:
            rd_error = RD_ERR_MESSAGE; /* out of sync */
            return;
        }
    }
}

int main(void) {
    enum { ST_NONE, ST_NO_USB, ST_NO_HOST } shown = ST_NONE;

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
            poll_keys();
            continue;
        }

        poll_keys();
        if (quit) break;

        /* Runs for as long as the host keeps talking; shows the desktop. */
        rd_error = RD_ERR_NONE;
        serve();
        if (quit) break;
        if (rd_error) last_error = rd_error;

        /* Host missing, stalled, or out of sync: resynchronise from scratch. */
        if (picture) {
            picture = false;
            shown = ST_NONE;
        }
        if (has_srl && shown != ST_NO_HOST) {
            status("USB connected. Waiting for host...",
                   "Run the host program on the PC.");
            shown = ST_NO_HOST;
        }
        rx_drain();
    }

    if (has_srl) {
        uint8_t i;
        tx_byte(MSG_BYE);
        for (i = 0; i < 100; i++) usb_HandleEvents(); /* let the goodbye go out */
    }

    usb_Cleanup();
    set_mode(8);
    gfx_End();
    return 0;
}
