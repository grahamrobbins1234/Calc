/*
 * ESP32-S3 Wi-Fi bridge for the TI-84 Plus CE remote desktop client.
 *
 * The ESP32 is the USB host. To the calculator it looks exactly like the PC
 * does in the cable setup, so the calculator runs the same REMOTE program.
 * The ESP32 dials the computer over Wi-Fi and shuttles bytes both ways,
 * inflating the compressed host -> calculator stream on the way through.
 *
 *   calculator <== USB serial ==> ESP32-S3 <== TCP over Wi-Fi ==> PC host
 */
#include <stdatomic.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "usb/cdc_acm_host.h"
#include "usb/usb_host.h"

#include "link.h"

/* The calculator's USB serial identity while REMOTE is running (srldrvce). */
#define CALC_VID 0x16C0
#define CALC_PID 0x05E1

#define USB_CHUNK 1024
#define USB_TX_TIMEOUT_MS 2000
#define NET_TIMEOUT_S 5
#define RETRY_DELAY_MS 2000

static const char *TAG = "bridge";

static EventGroupHandle_t s_wifi_events;
#define WIFI_READY BIT0

static SemaphoreHandle_t s_calc_lock; /* guards s_calc */
static cdc_acm_dev_hdl_t s_calc;
static atomic_bool s_calc_gone;

static SemaphoreHandle_t s_sock_lock; /* guards s_sock for the calculator -> host direction */
static int s_sock = -1;

static link_rx_t s_rx;

/* ---- Wi-Fi ------------------------------------------------------------ */

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_events, WIFI_READY);
        ESP_LOGW(TAG, "Wi-Fi disconnected, retrying");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ESP_LOGI(TAG, "Wi-Fi connected");
        xEventGroupSetBits(s_wifi_events, WIFI_READY);
    }
}

static void wifi_start(void) {
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    wifi_config_t config = {0};

    s_wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event, NULL));

    strlcpy((char *)config.sta.ssid, CONFIG_BRIDGE_WIFI_SSID, sizeof config.sta.ssid);
    strlcpy((char *)config.sta.password, CONFIG_BRIDGE_WIFI_PASSWORD, sizeof config.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE)); /* latency matters more than power */
    ESP_ERROR_CHECK(esp_wifi_start());
}

/* ---- Calculator (USB) --------------------------------------------------- */

/* Calculator -> host. Runs in the CDC driver's task. */
static bool on_calc_data(const uint8_t *data, size_t len, void *arg) {
    xSemaphoreTake(s_sock_lock, portMAX_DELAY);
    if (s_sock >= 0) {
        while (len) {
            int sent = send(s_sock, data, len, 0);
            if (sent <= 0) break; /* the network task notices the dead socket itself */
            data += sent;
            len -= sent;
        }
    }
    xSemaphoreGive(s_sock_lock);
    return true;
}

static void on_calc_event(const cdc_acm_host_dev_event_data_t *event, void *arg) {
    if (event->type == CDC_ACM_HOST_DEVICE_DISCONNECTED) {
        atomic_store(&s_calc_gone, true); /* closed by calc_task, outside the driver's task */
    } else if (event->type == CDC_ACM_HOST_ERROR) {
        ESP_LOGW(TAG, "USB serial error %d", event->data.error);
    }
}

/* Host -> calculator. */
static bool calc_write(const uint8_t *data, size_t len, void *ctx) {
    xSemaphoreTake(s_calc_lock, portMAX_DELAY);
    while (len && s_calc && !atomic_load(&s_calc_gone)) {
        size_t n = len < USB_CHUNK ? len : USB_CHUNK;
        if (cdc_acm_host_data_tx_blocking(s_calc, data, n, USB_TX_TIMEOUT_MS) != ESP_OK) break;
        data += n;
        len -= n;
    }
    xSemaphoreGive(s_calc_lock);
    /* Data that could not be delivered is dropped: the calculator times out
       and asks the host for a full frame, which resynchronises everything. */
    return true;
}

static void usb_lib_task(void *arg) {
    for (;;) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
    }
}

static void calc_task(void *arg) {
    const cdc_acm_host_device_config_t config = {
        .connection_timeout_ms = 1000,
        .out_buffer_size = USB_CHUNK,
        .in_buffer_size = 512,
        .event_cb = on_calc_event,
        .data_cb = on_calc_data,
    };

    for (;;) {
        if (s_calc) {
            if (atomic_load(&s_calc_gone)) {
                cdc_acm_dev_hdl_t gone;
                xSemaphoreTake(s_calc_lock, portMAX_DELAY);
                gone = s_calc;
                s_calc = NULL;
                xSemaphoreGive(s_calc_lock);
                cdc_acm_host_close(gone);
                ESP_LOGI(TAG, "Calculator unplugged");
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        cdc_acm_dev_hdl_t dev = NULL;
        if (cdc_acm_host_open(CALC_VID, CALC_PID, 0, &config, &dev) != ESP_OK) {
            continue; /* not plugged in, or REMOTE is not running yet */
        }
        cdc_acm_host_set_control_line_state(dev, true, false);
        atomic_store(&s_calc_gone, false);
        xSemaphoreTake(s_calc_lock, portMAX_DELAY);
        s_calc = dev;
        xSemaphoreGive(s_calc_lock);
        ESP_LOGI(TAG, "Calculator connected");
    }
}

/* ---- Host (TCP) ------------------------------------------------------------ */

static bool recv_exact(int sock, uint8_t *buf, size_t len) {
    while (len) {
        int n = recv(sock, buf, len, 0);
        if (n <= 0) return false;
        buf += n;
        len -= n;
    }
    return true;
}

static int host_connect(void) {
    struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM};
    struct addrinfo *res = NULL;
    struct timeval timeout = {.tv_sec = NET_TIMEOUT_S};
    char port[8];
    int sock, on = 1;

    snprintf(port, sizeof port, "%d", CONFIG_BRIDGE_PORT);
    if (getaddrinfo(CONFIG_BRIDGE_HOST, port, &hints, &res) != 0 || !res) {
        ESP_LOGW(TAG, "Cannot resolve %s", CONFIG_BRIDGE_HOST);
        return -1;
    }
    sock = socket(res->ai_family, res->ai_socktype, 0);
    if (sock >= 0) {
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
        if (connect(sock, res->ai_addr, res->ai_addrlen) != 0) {
            close(sock);
            sock = -1;
        }
    }
    freeaddrinfo(res);
    if (sock < 0) return -1;

    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &on, sizeof on);
    setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof on);
    return sock;
}

/* Prove we know the key; learn whether the host will compress. */
static bool host_handshake(int sock, bool *deflate) {
    uint8_t challenge[LINK_CHALLENGE_LEN], response[LINK_RESPONSE_LEN], accepted;

    if (!recv_exact(sock, challenge, sizeof challenge)) return false;
    if (!link_make_response(challenge, CONFIG_BRIDGE_KEY, LINK_FLAG_DEFLATE, response)) {
        ESP_LOGW(TAG, "Host speaks a different protocol version");
        return false;
    }
    if (send(sock, response, sizeof response, 0) != sizeof response) return false;
    if (!recv_exact(sock, &accepted, 1)) {
        ESP_LOGW(TAG, "Host rejected us - do the keys match?");
        return false;
    }
    *deflate = accepted & LINK_FLAG_DEFLATE;
    return true;
}

static void net_task(void *arg) {
    static uint8_t buf[2048];

    for (;;) {
        bool deflate = false;
        int sock;

        xEventGroupWaitBits(s_wifi_events, WIFI_READY, pdFALSE, pdTRUE, portMAX_DELAY);
        sock = host_connect();
        if (sock < 0 || !host_handshake(sock, &deflate)) {
            if (sock >= 0) close(sock);
            vTaskDelay(pdMS_TO_TICKS(RETRY_DELAY_MS));
            continue;
        }
        ESP_LOGI(TAG, "Connected to host %s:%d%s", CONFIG_BRIDGE_HOST, CONFIG_BRIDGE_PORT,
                 deflate ? " (compressed)" : "");

        /* From here on, long silences are normal (no calculator plugged in). */
        struct timeval forever = {0};
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &forever, sizeof forever);

        link_rx_init(&s_rx, deflate);
        xSemaphoreTake(s_sock_lock, portMAX_DELAY);
        s_sock = sock;
        xSemaphoreGive(s_sock_lock);

        for (;;) {
            int n = recv(sock, buf, sizeof buf, 0);
            if (n <= 0) break;
            if (!link_rx_feed(&s_rx, buf, n, calc_write, NULL)) {
                ESP_LOGW(TAG, "Corrupt stream from host");
                break;
            }
        }

        xSemaphoreTake(s_sock_lock, portMAX_DELAY);
        s_sock = -1;
        xSemaphoreGive(s_sock_lock);
        close(sock);
        ESP_LOGW(TAG, "Host connection lost, reconnecting");
        vTaskDelay(pdMS_TO_TICKS(RETRY_DELAY_MS));
    }
}

void app_main(void) {
    const usb_host_config_t usb_config = {.intr_flags = ESP_INTR_FLAG_LEVEL1};
    esp_err_t err = nvs_flash_init();

    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    s_calc_lock = xSemaphoreCreateMutex();
    s_sock_lock = xSemaphoreCreateMutex();

    wifi_start();

    ESP_ERROR_CHECK(usb_host_install(&usb_config));
    xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 20, NULL);
    ESP_ERROR_CHECK(cdc_acm_host_install(NULL));

    xTaskCreate(calc_task, "calc", 4096, NULL, 5, NULL);
    xTaskCreate(net_task, "net", 6144, NULL, 5, NULL);
}
