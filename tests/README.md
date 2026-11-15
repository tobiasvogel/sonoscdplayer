# Tests

All automated tests live in this folder. They run offline: no GPIO, SPI, CD drive or Sonos speaker is touched.

Run the whole suite from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Run a single file:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_power_button.py'
```

The tests of the statically built shutdown hook are skipped until it has been built with `sh tools/build_final_shutdown_hook.sh`.