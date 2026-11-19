/* Pure reply logic of apds9306d (no I2C, no sockets), shared with the tests.
 *
 * The daemon answers every client with one line:
 *   "<lux>\n"          a sample not older than the stale limit
 *   "ERR not-ready\n"  no sample since the sensor was (re)configured
 *   "ERR stale\n"      the last sample is too old (sensor stopped measuring)
 *   "ERR i2c\n"        the last I2C access failed; the daemon keeps retrying
 */
#ifndef APDS9306_LOGIC_H
#define APDS9306_LOGIC_H
#include <stdint.h>
#include <stdio.h>

typedef struct {
    int i2c_error;          /* last I2C access failed */
    int valid;              /* at least one sample since (re)configuration */
    double lux;
    uint64_t sample_ms;     /* monotonic time of that sample */
} apds_state;

/* A sample is stale after several missed measurement periods, at least 1 s. */
static inline uint64_t apds_stale_limit_ms(double integration_ms) {
    double limit = integration_ms * 8.0;
    return limit < 1000.0 ? 1000 : (uint64_t)limit;
}

static inline int apds_reply(const apds_state *s, uint64_t now_ms, uint64_t stale_ms,
                             char *out, size_t size) {
    if (s->i2c_error)
        return snprintf(out, size, "ERR i2c\n");
    if (!s->valid)
        return snprintf(out, size, "ERR not-ready\n");
    if (now_ms < s->sample_ms || now_ms - s->sample_ms > stale_ms)
        return snprintf(out, size, "ERR stale\n");
    return snprintf(out, size, "%.2f\n", s->lux);
}
#endif
