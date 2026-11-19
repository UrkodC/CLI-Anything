// recipe: keys_write
// Writes the Motion Rotation of ONE clip (keys, or one static value) and reads it back, in one round trip.
// ARGS: {node_id: string (the clip, looked for in the expected sequence only),
//        expect_clip: {in_ticks: string, out_ticks: string} (the in and out points the check read, digits),
//        mode: "keys" | "static",
//        keys: [{ticks: string (MEDIA ticks, digits: the in point plus the time from the clip start), value: number}]
//              (mode keys: at least one, no two with the same ticks),
//        value: number (mode static),
//        expect: {project_path, sequence_id, sequence_name} (added by the write session)}
// Python computes every tick sum as an integer; this script never adds ticks.
// Reply: {before: {is_time_varying, count}, set_errors: [string], after: <__readKeys of keys.jsx> or null}
// - before: what Rotation held before the first write (each read in a try; null when it would not say);
// - set_errors: the text of every host write that threw, and of a read back that failed;
// - after: Rotation read back after the writes; null when the read back failed (set_errors then says why).
//
// Every refusal is a "precondition:" error before the first host write, as in find.jsx: the ARGS are well formed,
// __expect (the project and the sequence), the clip by node ID in that sequence and on a video track, its in point
// and out point equal to expect_clip as strings (a trimmed or slipped clip is refused), speed 1 and not reversed
// (both must be read), and Motion with Rotation at index 4 by name.
// From the first host write on nothing throws: each write sits in its own try, an error goes to set_errors, and
// the script always reads back and ends in __ok.
//
// What it writes, in this order. This is the route that ran on Premiere 27.0.0 in measures M5 (measures/m5_write.jsx
// of Plan 3, Task 3) and the route of paste_clip_attributes:
// - mode keys: setTimeVarying(false) only when Rotation reads as time varying (measure M3b: this clears every key),
//   setTimeVarying(true) (measure M1: it adds no key), then for each key in the given order addKey(time) and
//   setValueAtKey(time, value, true). A key whose addKey threw gets no setValueAtKey. Interpolation is the default of
//   addKey.
// - mode static: setTimeVarying(false) only when Rotation reads as time varying, then setValue(value, true).
// Needs find.jsx, values.jsx and keys.jsx before it (the CLI builds it with all three).
//
// Host calls, each a call of premiere-pro-mcp 1.25.0 (dist/):
// - the project, the sequence, the clip, the effect and the property: __expect, __clip, __effect and __property of
//   find.jsx (bridge/script-builder.js, line 3322, lines 554-564, 1070-1098 and 724-733; tools/keyframes.js, lines
//   182-186);
// - clip.inPoint, clip.outPoint, clip.getSpeed(), clip.isSpeedReversed(): __clipFacts of keys.jsx
//   (bridge/script-builder.js, lines 1922-1943; tools/inspection.js, lines 339-354);
// - prop.isTimeVarying(): readTimeVarying of paste_clip_attributes (tools/clipboard.js, lines 155-157: a throw counts
//   as false) and tools/keyframes.js, line 320;
// - new Time() with time.ticks = String(ticks): __keyTime of keys.jsx (makeTime, tools/clipboard.js, lines 158-162;
//   __clipKeyTime, bridge/script-builder.js, lines 1983-1987);
// - prop.setTimeVarying(false), prop.setTimeVarying(true), prop.addKey(time), prop.setValueAtKey(time, value, true):
//   tools/clipboard.js, lines 253-258;
// - prop.setValue(value, true): tools/keyframes.js, line 220;
// - prop.getKeys(): tools/clipboard.js, line 268 and tools/keyframes.js, line 329, through __readKeys of keys.jsx
//   (which also reads getValueAtKey and getValue).

var errText = function (e) {
  try { return String(e); } catch (eText) { return "an error that cannot be shown"; }
};
var isList = function (value) {
  return value !== null && typeof value === "object" && Object.prototype.toString.call(value) === "[object Array]";
};
var digitsOnly = function (value) {
  return typeof value === "string" && /^[0-9]+$/.test(value);
};
var finite = function (value) {
  return typeof value === "number" && isFinite(value);
};

// ---- the arguments ----
if (typeof ARGS.node_id !== "string" || ARGS.node_id === "") throw __precondition("node_id must be a text");
if (!ARGS.expect_clip || typeof ARGS.expect_clip !== "object" || !digitsOnly(ARGS.expect_clip.in_ticks) ||
    !digitsOnly(ARGS.expect_clip.out_ticks)) {
  throw __precondition("expect_clip needs in_ticks and out_ticks as strings of digits");
}
if (ARGS.mode !== "keys" && ARGS.mode !== "static") throw __precondition("mode must be keys or static, not " + ARGS.mode);
var planned = [];
if (ARGS.mode === "keys") {
  if (!isList(ARGS.keys) || ARGS.keys.length === 0) throw __precondition("mode keys needs a list of keys with at least one key");
  var seen = {};
  for (var n = 0; n < ARGS.keys.length; n++) {
    var one = ARGS.keys[n];
    if (!one || typeof one !== "object" || !digitsOnly(one.ticks)) throw __precondition("key " + n + " needs ticks, a string of digits");
    if (!finite(one.value)) throw __precondition("key " + n + " needs a value that is a finite number");
    if (seen.hasOwnProperty("$" + one.ticks)) throw __precondition("two keys have the ticks " + one.ticks);
    seen["$" + one.ticks] = true;
    planned.push({ticks: one.ticks, value: one.value});
  }
} else if (!finite(ARGS.value)) {
  throw __precondition("mode static needs a value that is a finite number");
}

// ---- the target, all checked before the first write ----
var seq = __expect(ARGS.expect);
if (!seq) throw __precondition("the script got no sequence ID");
var found = __clip(seq, {node_id: ARGS.node_id});
if (found.track_type !== "video") throw __precondition("this clip is on an audio track; Motion Rotation is a video property");
var clip = found.clip;
var facts = __clipFacts(clip);
if (facts.in_ticks !== ARGS.expect_clip.in_ticks) {
  throw __precondition("the in point of the clip is " + facts.in_ticks + " ticks, not " + ARGS.expect_clip.in_ticks +
                       " as the plan expects");
}
if (facts.out_ticks !== ARGS.expect_clip.out_ticks) {
  throw __precondition("the out point of the clip is " + facts.out_ticks + " ticks, not " + ARGS.expect_clip.out_ticks +
                       " as the plan expects");
}
if (facts.speed === null) throw __precondition("Premiere did not report the speed of the clip");
if (Math.abs(facts.speed - 1) >= 0.0001) {
  throw __precondition("the clip has a speed of " + facts.speed + "; keys are written at speed 1 only");
}
if (facts.reversed === null) throw __precondition("Premiere did not report whether the clip is reversed");
if (facts.reversed) throw __precondition("the clip is reversed; keys are written on a clip that is not reversed only");
var motion = __effect(clip, "Motion", null);
var rotation = __property(motion.component, 4, "Rotation");

// ---- what Rotation holds before the write (reads only) ----
var before = {is_time_varying: null, count: null};
try { before.is_time_varying = !!rotation.isTimeVarying(); } catch (eBefore) {}
try { before.count = __readKeys(rotation).keys.length; } catch (eCount) {}

// ---- the write: from here on nothing throws ----
var setErrors = [];
var varying = false;
try { varying = !!rotation.isTimeVarying(); } catch (eVarying) { varying = false; }
if (varying) {
  try { rotation.setTimeVarying(false); } catch (eOff) { setErrors.push("setTimeVarying(false): " + errText(eOff)); }
}
if (ARGS.mode === "keys") {
  try { rotation.setTimeVarying(true); } catch (eOn) { setErrors.push("setTimeVarying(true): " + errText(eOn)); }
  for (var w = 0; w < planned.length; w++) {
    var where = "key " + w + " at " + planned[w].ticks + " ticks: ";
    var time = null;
    var added = false;
    try {
      time = __keyTime(planned[w].ticks);
      rotation.addKey(time);
      added = true;
    } catch (eAdd) {
      setErrors.push(where + "addKey: " + errText(eAdd));
    }
    if (!added) continue;
    try {
      rotation.setValueAtKey(time, planned[w].value, true);
    } catch (eValue) {
      setErrors.push(where + "setValueAtKey: " + errText(eValue));
    }
  }
} else {
  try { rotation.setValue(ARGS.value, true); } catch (eSet) { setErrors.push("setValue: " + errText(eSet)); }
}

// ---- the read back ----
var after = null;
try {
  after = __readKeys(rotation);
} catch (eRead) {
  setErrors.push("read back: " + errText(eRead));
}

return __ok({before: before, set_errors: setErrors, after: after});
