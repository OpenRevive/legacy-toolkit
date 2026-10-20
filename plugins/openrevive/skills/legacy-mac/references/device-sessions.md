# Shared device supervisor

OpenRevive 0.8 runs a persistent Python supervisor on the modern controller.
Lion remains the SSH/usbmux relay; the phone stays connected to Lion. Node 20+
and Python 3.9+ run locally. Native UIKit/HID remains 0.5; no new phone daemon,
respring, direct controller USB or service restart is required.

## Ownership and connections

Independent MCP servers and the JSON-lines CLI share one authenticated local
Unix socket, private token and SQLite journal in `~/.config/openrevive/supervisor`.
`OPENREVIVE_SUPERVISOR_ROOT` selects a private test instance. One actor owns the
control channel per configuration/host/UDID; separate actors own telemetry and
frames. The writer lock is per physical UDID, including aliases. Another writer
fails with its owner's operation ID and phase. There is no hidden input queue.
Screenshots and bounded logs may overlap a writer. Different devices have
separate actors; host build/install workflows require idle host channels.

`device_session` returns a server-minted logical `sessionId`. Save this handle
privately; `operation: attach` restores the consumer after an MCP restart. It is
a capability, not an input authorization. Each consumer has separate observation
bases and handles. Native snapshots can still be superseded by another fresh
capture; prefer `ui_perform` to capture and resolve immediately before input.

`operation: close`, `component: command|vnc|web|logs|crashes|all` closes that
component across this device's shared actors. `transport` closes the host's
shared transport connections. Busy shared components refuse close. Closing an
MCP frontend only disconnects that consumer; it does not kill a device executor.
An idle supervisor upgrades to changed package sources; a busy one refuses the
upgrade until its executor returns. Reload old plugin clients to join this
supervisor. Older cached plugin versions do not implement its recovery fence.

## Observe, perform and workflow

`ui_observe` captures a fresh guarded native tree and returns compact visible,
labelled/actionable nodes plus context, coverage and a revision. `nodeLimit`
(1..150) and `byteLimit` (2048..65536) bound its presentation. If mandatory context
alone exceeds the limit, the response says so. `sourceJsonBytes` measures the
normalized private tree, `returnedBytes` the projected JSON; neither measures
USB wire traffic or model tokens. Unknown consumer diff bases resynchronize to a
full compact view. Secure field values are redacted. The last 16 revisions per
consumer are retained; consumer/actor caches are bounded.

`cached: true` returns this consumer's last historical observation while a writer
runs, without device I/O. Historical coordinates never authorize input.
`read_ui_tree` and `find_ui` remain available for full private snapshots.

`ui_perform` requires the expected foreground `bundle`, an exact `selector`, a
typed action/value and an `expect` condition. Selector fields are label,
identifier, role, owner and className; `within` scopes to an ancestor up to three
levels. Resolve against a fresh complete screen, reject ambiguous/disabled/
covered targets and preserve the existing native owner, process, snapshot,
geometry and overlay guards. There is no fuzzy selector or fallback click.

Conditions are `present`, `absent`, `value` and `foreground`. Typed value checks
can use `attribute` such as isOn, enabled or contentOffsetPoints. Incomplete
coverage cannot establish absence or a unique mutation target. `mode: ensure`
skips dispatch when the predicate already holds. Otherwise input is sent once,
then observed locally; default `transition: becomes_true` requires false→true,
while `any` accepts an already true postcondition. `stableMs` (0..2000, default
100) requires the predicate to remain true across observations for that interval;
it does not prove continuous device state between samples.

`timeout` is a total controller budget, checked between bridge primitives. A
primitive already in flight can exceed it until its bounded bridge call returns.
`maxDispatches: 1` applies to one perform. Returned codes distinguish VERIFIED,
ALREADY_SATISFIED, guard failures, BUDGET_EXCEEDED, VERIFICATION_TIMEOUT,
CANCELLED and OUTCOME_UNKNOWN; `effect` reports none/dispatched/partial/unknown.
The result includes action receipt and final bounded observation. Declared UI
success is separate from application/server persistence; read an independent
receipt when persistence matters.

`ui_workflow` holds one writer for a bounded scenario. Steps: assert, observe,
ensure_context (explicit app launch), perform, wait, scroll_find, branch and
repeat. Bounds: 20 steps per list, two nested levels, 60 expanded steps, 30 input
primitives, 120 seconds total, repeat ≤5, scroll-find ≤10 scrolls. Scroll-find
uses native content offsets/ranges and fresh observations; virtualized content
must be exposed by the app. Results preserve completed/failing steps and
cumulative effects. Completed inputs are never repeated after a later failure.

Legacy `observe_ui`, `act_ui` and `act_and_wait` remain compatible. Their native
snapshots expire after 30 seconds and are single use. `act_and_wait` retains its
legacy polling-only timeout and optional `changed` predicate.

## Durable operations, cancellation and handover

Before important input, call `device_operation` with `action: reserve` and pass
the returned `operationId` to ui_perform/ui_workflow. IDs are issued by the
supervisor, not invented by a client. A terminal duplicate with the same semantic
request returns its retained result without input. Different requests conflict;
pruned or old execution identities expire. Detailed completed operations retain
the most recent 2000 records; the durable high-water mark still prevents reuse.
Traces redact entered text, JavaScript, argv and command output. The private
result journal can contain observed application data; protect the runtime folder.

`status`, `list` and `trace` remain queryable during an action. `cancel` requests
cancellation. The writer stays occupied until the executing bridge call returns
and the worker acknowledges a boundary. `cancelRequested` is distinct from
`executorQuiescent`. Client disconnect also requests cancellation; an MCP timeout
is never proof that input stopped. Reconnect/attach and inspect the original ID.

Lost actor/response evidence becomes OUTCOME_UNKNOWN and quarantines the device.
`reconcile` checks durable remote completion receipts and, when recorded, native
serial-port ping barriers or process termination. It can confirm execution
quiescence while leaving business outcome unknown. It never repeats input.
Unknown VNC input has no executor acknowledgement and remains blocked for
operator reconciliation. No native fencing, native event subscription or
cancellation of already queued OS/application effects is claimed. Cooperating
controller locks and guards are the execution boundary.

`handover` blocks new input, requests cancellation and reports
`manualControlReady` only after the controller writer finishes. After manual
work, `resume` advances generation and discards control actors, invalidating old
snapshots/web references; acquire fresh observations. Uncertain execution must
be reconciled before handover/resume. Do not operate another old cached client
outside these cooperating locks during recovery.

## Web contexts and diagnostics

`ui_contexts` discovers native owners and actual web pages without activating
them. Native-view association remains unverified. Standard remote Web Inspector
requires iOS 6 and Safari Advanced → Web Inspector enabled. iOS 5 is explicitly
unsupported; UIWebView inspection depends on the app.

`web_inspect pages` produces opaque page IDs. `context` binds a page to its main
frame/loader document epoch and native foreground/PID/process/system-overlay
evidence. `dom`, `evaluate` and `events` accept contextId/documentEpoch.
Navigation, DOM document replacement, app disconnect or a changed loader rejects
old contexts, including same-URL navigation. JavaScript mutations recheck native
guards immediately before and after dispatch and hold the device writer. Another
inspector's connection is not stolen. Native page/view identity and subframe
mapping are not inferred. A legacy pageId call captures a fresh context; reuse an
explicit context to require the earlier document. JavaScript is never replayed.

`get_capabilities` reports individual available/unavailable/busy/unsupported/
untested service evidence and remedies. Reachability alone does not prove UI or
DOM acceptance. `read_logs` streams bounded syslog; `list_crashes`/`read_crash`
read existing reports without moving/deleting them; `capture_network` saves a
bounded private PCAP with honest header/filter limitations. No automatic repair
or package install occurs during a capability read. See [capture](capture.md)
for incremental VNC, independent screenshotr and cropping semantics.

Protocol references: [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice/tree/master/include/libimobiledevice),
[WebKit iOS 6 schema](https://github.com/WebKit/WebKit/blob/d9c4c5fcd15cc8a72a1bbecc143acfcacefea6fa/Source/WebInspectorUI/Versions/Inspector-iOS-6.0.json),
[WIR envelopes](https://github.com/google/ios-webkit-debug-proxy/blob/v1.9.2/src/rpc.c).
No third-party implementation or new device package is bundled.

## CLI and operation

```sh
./bin/openrevive device --udid "$UDID" session \
  --request '{"method":"device_session","args":{}}' \
  --request '{"method":"ui_observe","args":{"nodeLimit":20}}'
```

Without --request, stream JSON-lines `{ "method": ..., "args": ... }` on stdin.
The CLI fixes host/UDID and shares the same supervisor. EOF closes the client,
not the shared executor. PNG data is base64 _image on the CLI and inline through
MCP. `device_artifact` retrieves a private content-addressed image handle issued
to the logical session. At most 128 screenshot artifacts are retained; retrieval
checks ownership and content hash. Crops report full-screen imageOrigin.

For troubleshooting, run `python3 PLUGIN_ROOT/mcp/supervisor.py --health`.
`--shutdown` refuses active jobs. Logs, auth token and journal are private; do not
paste them publicly. Existing setup/build CLI commands remain available.
