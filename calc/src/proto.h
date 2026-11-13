/*
 * Wire protocol shared by the calculator client and the PC host.
 * The Python host mirrors these values in host/tiremote/protocol.py.
 *
 * Calculator -> host is a plain byte stream of the messages below.
 *
 * Host -> calculator is a stream of packets:
 *
 *     u16 n            bit 15: payload is an LZ4 block; bits 0-14: bytes on the wire
 *     u16 plain_len    only if LZ4: size after decompression
 *     u8  payload[n]
 *
 * The (decompressed) payloads concatenate into a stream of the host
 * messages below. A message may span packets.
 *
 * A packet with bit 14 set instead is out-of-band: 3 bytes (u16 x, u8 y)
 * giving the pointer position, acted on the moment it arrives - even in the
 * middle of a frame - so the pointer never waits behind picture data.
 */
#ifndef PROTO_H
#define PROTO_H

#define PROTO_VERSION 3

/* Calculator -> host */
#define MSG_HELLO 0xA1 /* + 1 byte version. "Start over: send mode, palette and a full frame." */
#define MSG_ACK   0xA2 /* One frame has been drawn; the host may send another. */
#define MSG_KEYS  0xA3 /* + 7 bytes: keypad matrix groups 1..7 */
#define MSG_BYE   0xA4 /* Client is exiting. */

/* The host may have this many frames sent but not yet acknowledged. */
#define FRAME_WINDOW 2

/* Host -> calculator */
#define MSG_MODE    'M' /* + 1 byte bits per pixel (8, 4 or 1) */
#define MSG_PALETTE 'P' /* + 1 byte count (0 = 256) + count little-endian 1555 colours */
#define MSG_FRAME   'F' /* + frame ops, terminated by OP_END */

#define CURSOR_HIDDEN 0xFF /* pointer y meaning "do not draw it" */

/*
 * Frame ops walk the framebuffer bytes from the top-left.
 * Header byte: op in the top two bits, byte count in the low six bits.
 * A count of 0 means a 16-bit little-endian count follows the header.
 */
#define OP_SKIP 0x00 /* leave `count` bytes unchanged */
#define OP_RUN  0x40 /* + 1 byte value: fill `count` bytes */
#define OP_LIT  0x80 /* + `count` bytes: copy verbatim */
#define OP_END  0xC0 /* end of frame */
/*
 * OP_MOVE: + u16 xb + u8 y + u16 wb + u8 h + s8 dx + s8 dy.
 * Copies the block of rows [y, y+h) x byte columns [xb, xb+wb) to a place
 * dx bytes to the right and dy rows down (negative: left / up), like
 * scrolling or panning. Does not advance the position.
 */
#define OP_MOVE 0xC1

#define PKT_LZ4 0x8000
#define PKT_OOB 0x4000
#define PKT_OOB_LEN 3
#define PKT_MAX 8192 /* largest payload, on the wire or decompressed */

#define KEY_GROUPS 7

#define SCREEN_W 320
#define SCREEN_H 240

#endif
