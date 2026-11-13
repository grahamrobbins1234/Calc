#ifndef SHA256_H
#define SHA256_H

#include <stddef.h>
#include <stdint.h>

#define SHA256_LEN 32

typedef struct {
    uint32_t state[8];
    uint64_t length;
    uint8_t block[64];
    size_t fill;
} sha256_t;

void tird_sha256_init(sha256_t *s);
void tird_sha256_update(sha256_t *s, const uint8_t *data, size_t len);
void tird_sha256_final(sha256_t *s, uint8_t out[SHA256_LEN]);

void tird_hmac_sha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t msg_len,
                 uint8_t out[SHA256_LEN]);

#endif
