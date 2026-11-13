"""Wire protocol constants. Mirrors calc/src/proto.h."""

PROTO_VERSION = 3

# Calculator -> host
MSG_HELLO = 0xA1  # + 1 byte version
MSG_ACK = 0xA2  # one frame drawn
MSG_KEYS = 0xA3  # + 7 bytes keypad matrix (groups 1..7)
MSG_BYE = 0xA4

FRAME_WINDOW = 2  # frames the host may have sent but not yet acknowledged

# Host -> calculator (carried in packets, see encoder.packetize)
MSG_MODE = ord("M")  # + 1 byte bits per pixel
MSG_PALETTE = ord("P")  # + 1 byte count (0 = 256) + count * 2 bytes
MSG_FRAME = ord("F")  # + ops, OP_END terminated

CURSOR_HIDDEN = 0xFF

OP_SKIP = 0x00
OP_RUN = 0x40
OP_LIT = 0x80
OP_END = 0xC0
OP_MOVE = 0xC1  # + u16 xb + u8 y + u16 wb + u8 h + s8 dx + s8 dy

PKT_LZ4 = 0x8000
PKT_OOB = 0x4000  # out-of-band pointer position: u16 x + u8 y
PKT_MAX = 8192

KEY_GROUPS = 7

LCD_W = 320
LCD_H = 240

# USB IDs the calculator presents while the client is running (srldrvce's
# CDC ACM descriptors).
USB_VID = 0x16C0
USB_PID = 0x05E1
