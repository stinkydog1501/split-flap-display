# MicroPython Port

This directory contains the MicroPython version of the split-flap display firmware.
Copy the contents of `micropython/app` to the root of the ESP32 filesystem.

## Files

- `boot.py` loads saved settings and brings up Wi-Fi. If station mode cannot connect, it starts the `Split Flap Display` access point.
- `main.py` starts the display controller, MQTT client, and Microdot web server.
- `static/*` contains the HTML, CSS, JS, and Alpine/Tailwind assets for the web interface.

## Dependencies

Install or copy these MicroPython libraries to the device:

- Microdot core package.
- `umqtt.simple` if MQTT support is needed.

Microdot's MicroPython installation docs recommend copying the required source files from its GitHub repository to the device. This app needs the base Microdot package.

## Updating Web Assets

From the repository root:

```sh
npm run assets
node micropython/tools/stage_web_assets.mjs
```

The existing `npm run assets:micropython` script runs those steps together.


## Notes

The Arduino OTA flow does not have a direct standard MicroPython equivalent, so the `otaPass` setting is preserved for API compatibility but is not used by this port.

The `mdns` value is applied as the network hostname when the MicroPython port supports it. Full `.local` mDNS resolution depends on the firmware build and network environment.

MicroPython does not provide the ESP-IDF POSIX timezone support used by Arduino `configTzTime()`. The port reads the same timezone setting and applies the base POSIX offset for date/time display; daylight-saving transition rules are ignored.
