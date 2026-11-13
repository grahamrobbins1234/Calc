/*
 * Wire protocol shared by the calculator client and the PC host.
 * The Python host mirrors these values in host/tiremote/protocol.py.
 */
#ifndef PROTO_H
#define PROTO_H

#define PROTO_VERSION 1

/* Calculator -> host */
#define MSG_HELLO 0xA1 /* + 1 byte version. "Send me the palette and a full frame." */
#define MSG_REQ   0xA2 /* "Send me the next (delta) frame." */
#define MSG_KEYS  0xA3 /* + 7 bytes: keypad matrix groups 1..7 */
#define MSG_BYE   0xA4 /* Client is exiting. */

/* Host -> calculator */
#define MSG_PALETTE 'P' /* + 512 bytes: 256 little-endian 1555 colours */
#define MSG_FRAME   'F' /* + frame ops, terminated by OP_END */

/*
 * Frame ops walk the 320x240 8bpp framebuffer from the top-left pixel.
 * Header byte: op in the top two bits, pixel count in the low six bits.
 * A count of 0 means a 16-bit little-endian count follows the header.
 */
#define OP_SKIP 0x00 /* leave `count` pixels unchanged */
#define OP_RUN  0x40 /* + 1 byte colour: fill `count` pixels */
#define OP_LIT  0x80 /* + `count` bytes: copy pixels verbatim */
#define OP_END  0xC0 /* end of frame */

#define KEY_GROUPS 7

#endif
