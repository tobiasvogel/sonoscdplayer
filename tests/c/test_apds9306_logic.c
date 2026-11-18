/* Reply logic of apds9306d; compiled and run by tests/test_apds9306_daemon.py. */
#include <assert.h>
#include <string.h>
#include "apds9306_logic.h"

static const char *reply(apds_state s, uint64_t now, uint64_t stale) {
    static char out[64];
    apds_reply(&s, now, stale, out, sizeof out);
    return out;
}

int main(void) {
    apds_state fresh = { .valid = 1, .lux = 12.345, .sample_ms = 1000 };
    assert(strcmp(reply(fresh, 1500, 1000), "12.35\n") == 0);
    assert(strcmp(reply(fresh, 2000, 1000), "12.35\n") == 0);      /* at the limit */
    assert(strcmp(reply(fresh, 2001, 1000), "ERR stale\n") == 0);  /* sensor stopped */
    assert(strcmp(reply(fresh, 999, 1000), "ERR stale\n") == 0);   /* clock oddity */
    apds_state none = { 0 };
    assert(strcmp(reply(none, 0, 1000), "ERR not-ready\n") == 0);
    apds_state broken = fresh;
    broken.i2c_error = 1;
    assert(strcmp(reply(broken, 1500, 1000), "ERR i2c\n") == 0);  /* never the old value */
    assert(apds_stale_limit_ms(25) == 1000);
    assert(apds_stale_limit_ms(400) == 3200);
    return 0;
}
