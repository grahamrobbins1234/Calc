"""Wire protocol constants. Mirrors calc/src/proto.h."""

PROTO_VERSION = 6
COMPATIBLE_VERSIONS = (4, 5, 6)  # calculator programs this host can talk to

# Calculator -> host
MSG_HELLO = 0xA1  # + 1 byte version
MSG_ACK = 0xA2  # one frame drawn
MSG_KEYS = 0xA3  # + 7 bytes keypad matrix (groups 1..7)
MSG_BYE = 0xA4

FRAME_WINDOW = 2  # frames the host may have sent but not yet acknowledged

# Host -> calculator (carried in packets, see encoder.packetize)
MSG_MODE = ord("M")  # + 1 byte: bits per pixel, plus MODE_HALF
MSG_SCALE = ord("S")  # + 1 byte: 1 = MODE_HALF pictures are half size, 2 = quarter size (v6+)
MODE_HALF = 0x80  # frames are 160x120, shown with every pixel doubled
MODE_TURN_SHIFT = 5  # bits 5-6: quarter-turns clockwise, so the pointer is drawn turned (v5+)
MODE_BPP_MASK = 0x1F
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
