# UIKit text input, 16 October 2026

The DNS editing investigation exposed a general input issue. The old `set-text`
assigned the text property and manually emitted change callbacks without entering
the normal editing lifecycle. After explicit focus, the unified layout guard
repeatedly rejected input while iOS text selection decoration was active.

The bridge now focuses the actual guarded UITextField/UITextView, selects its
document and uses the actual iOS 6 keyboard's `addInputString:` or `handleDelete`.
Direct `insertText:` was tested and found to bypass callbacks needed by these
controls; it is not used as a fallback. The keyboard API is checked at runtime.
UIKit handles delegate
validation and change events. A rejected/transformed value is reported explicitly.
The field remains focused. `end-editing` resigns first responder through UIKit,
honors an end-editing veto and permits the application to commit its value.
Snapshots are consumed even when a focus/edit attempt is rejected after a side
effect. New trees expose `isFirstResponder`; secure text stays redacted.

The layout token ignores only the known noninteractive text selection decoration
classes. Interactive views, geometry, window levels, ownership, foreground,
overlays, expiration and hit tests remain checked. It does not retry stale input.

`python3 tests/check_text_input.py native` compiles the exact production bridge
with Xcode 4.6 for armv7 and executes that source inside a private iOS 6.1
Simulator application on BlackLion. The app independently records begin/change/end
events, vetoes, Unicode replacement/deletion, multiline input, secure redaction,
saved values from the end-edit delegate, and stale/moved/covered-target rejection.
It also changes the real UIKit selection decoration to check layout stability,
then makes it interactive to prove that interactive elements remain guarded.
The receipt and native app image are in `.artifacts/text-input/` with source hashes.
`local` runs package, host schema, MCP schema and backend regressions;
`evidence` binds the saved native result to the current source.

This scoped fix does not change DNS or restart the physical iPhone being used by
the main investigation. Activation in that phone's global bridge is a separate
step: update the installed plugin, then use its existing `setup-uikit --respring`
workflow during an exclusive device window. Until activation, a running old
bridge retains the old behavior and does not advertise `end-editing`.
