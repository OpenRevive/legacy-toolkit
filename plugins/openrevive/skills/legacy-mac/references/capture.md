# Capture freshness on legacy iOS

VNC connectivity, receipt of pixels and an up-to-date device screen are separate
observations. Veency has no frame timestamp or sequence number. A full reply
can contain an old surface, and a static current screen can legitimately return
the same pixels repeatedly.

## New-connection warm-up

In the exact v0.9.3500 source, Veency registers clients synchronously on the
device's UI thread (`waitUntilDone:YES`). The earlier asynchronous-registration
explanation was incorrect. Its capture hook refreshes a stored framebuffer only
on eligible layer-zero rendering callbacks while clients are connected. A full
RFB request resends that buffer; it does not request a new system screenshot.
A new connection can therefore receive the previous surface until a later render
callback replaces it. Short connections leave gaps during which capture is idle.

The two-second warm-up is a compatibility workaround, not a guarantee of freshness.
Persistent 0.8 sessions retain the stream and framebuffer after warm-up, reducing
new-client gaps. Fixed delays cannot repair a stuck producer or a wrong render
layer. Raw frame transfer can also stall independently of freshness.

On 20 October 2026, an iPhone3,1 running iOS 6.1.3 returned the same obsolete map
grid for two full requests after reconnecting; a third request on the same
connection returned the actual map and route. The connection stayed alive for
roughly three seconds. A repaired new connection subsequently returned the
current WakeApp screen. This is a bounded observation, not a guarantee that all
Veency freeze conditions are repaired by waiting.

`iphone_vnc.VNC.capture` discards an initial complete frame, keeps a new session
alive for two seconds and requests another full framebuffer. The default cold
capture deadline is eight seconds. A warmed session uses incremental updates,
applying bounded raw/zlib rectangles to its previous complete framebuffer.

An ordinary incremental update deadline (default 0.75s) returns cached pixels
without closing the stream, preserving partial headers/payloads and an outstanding
request. A later capture resumes that reply. `incremental: false` explicitly
requests a complete update. EOF, malformed/oversized rectangles, incomplete cold
coverage or a failed full request closes the failed connection and preserves the
previous output file. There is no input or service restart to provoke a frame.

`crop: [x,y,width,height]` uses full-screen pixels. Cropping reduces PNG output;
it does not imply proportional RFB traffic reduction. Native screenshotr also
supports controller-side cropping. Images are atomic/private and can be fetched
by their session-owned content hash through `device_artifact`.

## Capture metadata

- `transport`: `vnc`.
- `framesReceived`: complete replies read during this capture.
- `warmupSeconds`: initial warm-up interval, or zero on a warmed connection.
- `elapsedMs`: controller capture duration.
- `pixelSha256`: digest of decoded RGB pixels, independent of PNG compression.
- `changedDuringWarmup`: whether the discarded and accepted pixel buffers differ.
- `frameReceivedAt` / `receivedAt`: controller acquisition time, retained for cache.
- `returnedAt`: time this response was produced; `ageMs` is time since acquisition.
- `cached` / `cacheStatus`: explicitly distinguishes an update from no reply.
- `pendingUpdate` / `partialWireBytes`: a reply still in flight, not a broken frame.
- `wireBytes` / `requestWireBytes`: actual received/sent RFB bytes this capture.
- `bytes`: returned PNG bytes, separate from wire measurements.
- `imageOrigin`, `screenWidth` / `screenHeight`: crop origin and full-screen size.
- `frameTimestampAvailable`: false; `freshness`: unverified.

CLI screenshot --vnc forwards capture metadata. MCP capturedAt is acquisition
time, not the time cached pixels were returned. Cropped bytes and unchanged image
hashes cannot prove device state or action completion.

## When pixels still disagree

1. Read a fresh native UIKit/unified tree and check the actual foreground owner,
   controls, lock state and overlays. The old screenshot is not a coordinate map.
2. Request another update on the warmed connection; explicitly close the VNC
   component if a full reconnect is needed. A static
   identical screen is valid; do not report a freeze solely from equal hashes.
3. Prefer native screenshotr when it is available. It includes compositor and
   overlay content that VNC may omit. If missing, `device prepare` can mount the
   matching developer image from the user's Xcode; this is explicit setup, never
   an automatic fallback that changes the phone during a read-only screenshot.
4. If no independent current picture is available, use only actions supported by
   fresh guarded native nodes, or report the capture limitation. Do not bypass
   an owner/occlusion/freshness rejection with guessed coordinates.

Do not routinely respring, reboot, reinstall Veency or alter its password to
recover a screenshot. Preserve existing UI state and other active controllers.

Upstream context: Jay Freeman's Veency, with client registration and framebuffer
capture visible in the [maintained source fork](https://github.com/Torrekie/Veency/blob/master/Tweak.mm).
The plugin does not bundle or modify Veency source for this repair.
