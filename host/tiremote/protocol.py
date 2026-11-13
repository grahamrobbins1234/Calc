"""Wire protocol constants. Mirrors calc/src/proto.h."""

PROTO_VERSION = 1

# Calculator -> host
MSG_HELLO = 0xA1  # + 1 byte version
MSG_REQ = 0xA2
MSG_KEYS = 0xA3  # + 7 bytes keypad matrix (groups 1..7)
MSG_BYE = 0xA4

# Host -> calculator
MSG_PALETTE = ord("P")  # + 512 bytes
MSG_FRAME = ord("F")  # + ops, OP_END terminated

OP_SKIP = 0x00
OP_RUN = 0x40
OP_LIT = 0x80
OP_END = 0xC0

KEY_GROUPS = 7

LCD_W = 320
LCD_H = 240
FB_SIZE = LCD_W * LCD_H

# USB IDs the calculator presents while the client is running (srldrvce's
# CDC ACM descriptors).
USB_VID = 0x16C0
USB_PID = 0x05E1
