# Wi-Fi bridge (ESP32-S3)

Lets the calculator reach the computer over the network instead of a cable:

```
 TI-84 Plus CE ◀── USB ──▶ ESP32-S3 ◀── Wi-Fi / TCP ──▶ computer
   REMOTE.8xp               this firmware                python -m tiremote --listen
```

The ESP32 acts as the USB host, so to the calculator it looks exactly like the
computer does in the cable setup. **The calculator runs the same `REMOTE`
program** — nothing on the calculator changes between cable and Wi-Fi.

> **Status:** this firmware compiles in CI, and its protocol code (key check
> and decompression) is tested against the host program. It has not been run
> on a physical ESP32 or calculator yet. Expect to do some debugging over the
> serial monitor the first time, especially around USB power (below).

## What you need

- A board with an **ESP32-S3** (or S2) and its native USB pins on a connector,
  e.g. an ESP32-S3-DevKitC-1. The original ESP32, the C3 and the C6 cannot be
  USB hosts and will not work.
- The calculator's own USB cable, plus an adapter so its USB-A end plugs into
  the board's native USB port (usually labelled `USB`, not `UART`).
- **5 V on that USB port.** The ESP32 is the host, so it must supply power to
  the cable; the calculator will not start USB otherwise. Most dev boards do
  not feed 5 V out of the native port by default. Check your board's
  documentation: some have a solder jumper for this, otherwise wire the
  board's 5 V pin to the port's VBUS. Power the board itself through its
  other USB port or the 5 V pin (a phone power bank works).

## Build and flash

Install [ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/)
(v5.3 or newer; CI builds with v6.1), then:

```sh
cd bridge
idf.py set-target esp32s3
idf.py menuconfig      # "TI-84 remote desktop bridge": Wi-Fi name and password,
                       # the computer's address, port and the shared key
idf.py -p PORT flash monitor
```

The settings are compiled into the firmware, so rebuild after changing them.

## Run

On the computer (same shared key as in `menuconfig`):

```sh
cd host
TIREMOTE_KEY="your shared key" python -m tiremote --listen
```

Then power the bridge, plug in the calculator and start `REMOTE`. The bridge
keeps retrying Wi-Fi, the computer and the calculator on its own, so the order
does not matter. The serial monitor shows what it is waiting for.

## Network and security

- The bridge **dials out** to the computer (default TCP port 8484), so the
  computer needs to accept incoming connections on that port — allow it in
  the firewall. To reach your computer from another network, forward the port
  on your router or, better, put both ends on a VPN.
- The computer only accepts a bridge that proves it knows the shared key
  (HMAC-SHA256 challenge), so strangers cannot take control of the mouse and
  keyboard. Use a long random key.
- The stream is **not encrypted**. Anyone who can watch the network traffic
  can see the screen and what is typed, and someone able to tamper with the
  connection after it is established could inject input. Use it on a network
  you trust, or over a VPN.

## What the ESP32 adds beyond networking

The host deflate-compresses everything it sends over Wi-Fi and the ESP32
inflates it before passing it to the calculator, which could never afford to
do that itself. On desktop content this cuts network traffic to roughly a
third to a half. That makes slow or remote links usable; it does not raise
the frame rate on a good network, because the limit there is the 12 Mbit/s
USB link into the calculator and the calculator's own CPU.

## How it fits together

- `main/main.c` — Wi-Fi, USB host (CDC-ACM), TCP client, and the two byte
  pumps between them.
- `main/link.c`, `main/link.h` — the handshake and the inflate stage. No
  ESP-IDF dependencies; `host/tests/test_network.py` compiles them natively.
- `main/sha256.c` — a small SHA-256/HMAC so the firmware does not depend on a
  particular ESP-IDF crypto API.
