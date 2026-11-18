// recipe: app_switch
// Reads and switches two app-wide things a frame export needs: the proxy display and the active sequence.
// ARGS: {action: "read" | "proxies" | "activate",
//        enabled: true or false (action "proxies"),
//        sequence_id: the ID of the sequence to make active (action "activate"),
//        expect: {project_path, sequence_id or null, sequence_name or null} (added by the write session)}
// Replies:
// - "read": {proxies: 1 | 0 | null, active_sequence: {id, name} | null,
//            sequence: {id, name, end_ticks} | null (the expected sequence; null when the session named none)}
//   end_ticks is the end of the sequence as a tick string of digits, or null when it cannot be read.
// - "proxies": {before: 1 | 0 | null, after: 1 | 0 | null, set_error: text | null}
// - "activate": {before: {id, name} | null, after: {id, name} | null, set_error: text | null}
// proxies is null when the value cannot be read or is not 1 or 0. after is the read-back in the same script.
//
// Every check comes first and is a "precondition:" refusal, which means nothing was changed: __expect (the
// project and, when the session named one, the sequence), the action, the value of enabled, the sequence to
// make active. After the first host write nothing can throw: the write and each read sit in their own try and
// the script ends in __ok.
//
// Host calls (no other is made):
// - app.getEnableProxies() (a number, 1 on and 0 off) and app.setEnableProxies(1 or 0): measured on Premiere 27
//   (measures.md section 2) and the server's manage_proxies toggle (dist/tools/export.js, lines 2635-2662);
// - app.project.activeSequence (read, and set to a sequence): measured on Premiere 27 (measures.md section 3)
//   and the server's set_active_sequence (dist/tools/project.js);
// - seq.end, a tick string: the server's list_sequences (dist/tools/discovery.js, line 107);
// - seq.sequenceID, seq.name and the sequence by ID: __expect and __sequenceById of find.jsx.

var EXPECTED = __expect(ARGS.expect);
var ACTION = ARGS.action;
if (ACTION !== "read" && ACTION !== "proxies" && ACTION !== "activate") {
  throw __precondition("the action must be read, proxies or activate, not " + ACTION);
}

var refOf = function (seq) {
  if (!seq) return null;
  var out = {"id": null, "name": null};
  try { out.id = String(seq.sequenceID); } catch (eId) {}
  try { out.name = String(seq.name); } catch (eName) {}
  return out;
};
var proxiesNow = function () {
  try {
    var value = Number(app.getEnableProxies());
    if (value === 1) return 1;
    if (value === 0) return 0;
  } catch (eProxies) {}
  return null;
};
var activeNow = function () {
  try { return refOf(app.project.activeSequence); } catch (eActive) { return null; }
};

if (ACTION === "read") {
  var shown = refOf(EXPECTED);
  if (shown) {
    shown.end_ticks = null;
    try {
      var end = String(EXPECTED.end);
      if (/^[0-9]+$/.test(end)) shown.end_ticks = end;
    } catch (eEnd) {}
  }
  // A throw here is a script failure: nothing was switched, and an unreadable active sequence must not pass
  // for "no active sequence".
  var active = refOf(app.project.activeSequence);
  return __ok({"proxies": proxiesNow(), "active_sequence": active, "sequence": shown});
}

if (ACTION === "proxies") {
  if (ARGS.enabled !== true && ARGS.enabled !== false) {
    throw __precondition("enabled must be true or false, not " + ARGS.enabled);
  }
  var proxiesBefore = proxiesNow();
  var proxiesError = null;
  try { app.setEnableProxies(ARGS.enabled ? 1 : 0); } catch (eSetProxies) { proxiesError = String(eSetProxies); }
  return __ok({"before": proxiesBefore, "after": proxiesNow(), "set_error": proxiesError});
}

// ACTION === "activate"
if (!__given(ARGS.sequence_id)) throw __precondition("the script got no sequence_id to make active");
var wanted = __sequenceById(ARGS.sequence_id);
if (!wanted) throw __precondition("no sequence has the ID " + ARGS.sequence_id);
var activeBefore = activeNow();
var activateError = null;
try { app.project.activeSequence = wanted; } catch (eActivate) { activateError = String(eActivate); }
return __ok({"before": activeBefore, "after": activeNow(), "set_error": activateError});
