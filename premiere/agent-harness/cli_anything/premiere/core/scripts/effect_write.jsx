// recipe: effect_write
// Writes one value to one effect property and reads it back, in one round trip.
// ARGS: {sequence_id: string,
//        target: {track_type: "video", track, clip} or {node_id}: the target as the plan names it,
//        node_id: string: the node ID that effect_resolve.jsx found at that target,
//        effect: string, effect_index: whole number or null (as the plan gives it),
//        found_effect_index: whole number: the effect index that effect_resolve.jsx found,
//        index: whole number, name: string (the exact display name), value: number, string or boolean,
//        tolerance: number (how near a read back of the zero step must be to 0, and an expect_old to the value),
//        expect_old: number, string or boolean, only on the first attempt of an entry that has one,
//        expect: {project_path, sequence_id, sequence_name} (added by the write session)}
// Reply: {before, zero_first, set_error, value, read_error}
// - before: prop.getValue() before anything is written (null when it throws or is not a plain value);
// - zero_first: true when 0 was written first because before > 0 and the value < 0 ("crosses zero");
// - set_error: the text of a setValue that threw, or "zero step failed: <error or read back>" when the zero step
//   did not take; then the value itself was not written;
// - value: prop.getValue() after the write (or after the failed zero step); read_error: the text when it threw.
// value and before are plain: a finite number, a string, a boolean or null.
//
// Every check comes before the first write, and each one is a "precondition:" refusal, which means nothing was
// changed: __expect (the project and the sequence), the sequence ID, the clip, the effect and the property with
// the finders of find.jsx, a clip on a video track (a node ID can name an audio clip), the clip's node ID against the one resolved, the effect index against the one resolved,
// the keyframes, the value, the tolerance, and (when ARGS has the key expect_old) the value before against
// expect_old with the rules of same_value in effects.py. After the first setValue nothing can throw: every host
// call sits in its own try and the script always ends in __ok.
//
// Host calls follow tests/gates/run_gates.py (WRITE_PROPERTY_BODY, which ran on Premiere 27) and the server's
// set_effect_property (dist/tools/keyframes.js):
// - the project, the sequence, the clip, the effect and the property: __expect, __clip, __effect, __property of
//   find.jsx; clip.nodeId: bridge/script-builder.js, lines 1070-1098;
// - prop.isTimeVarying(): the write refusal of tools/keyframes.js, lines 215-218, and of WRITE_PROPERTY_BODY;
//   a throw, or no such function, is refused too ("Parameter animation state could not be read", keyframes.js);
// - prop.getValue() before and after: tools/keyframes.js, line 228, and WRITE_PROPERTY_BODY;
// - prop.setValue(value, true): tools/keyframes.js, line 220, and WRITE_PROPERTY_BODY; also for the zero step.

var PLAIN_OK = {"number": true, "string": true, "boolean": true};
// A value as it may leave the script: a finite number, a string, a boolean, or null for anything else.
var plain = function (value) {
  try {
    var type = typeof value;
    if (type === "number") return isFinite(value) ? value : null;
    if (type === "string" || type === "boolean") return value;
  } catch (ePlain) {}
  return null;
};
var errorText = function (e) {
  try { return String(e); } catch (eText) { return "an error that cannot be shown"; }
};
// same_value of effects.py: two finite numbers within the tolerance, two equal strings, two equal booleans. A
// boolean is never the same as a number, and null is never the same as anything.
var sameValue = function (a, b, tol) {
  if (typeof a === "boolean" || typeof b === "boolean") {
    return typeof a === "boolean" && typeof b === "boolean" && a === b;
  }
  if (typeof a === "number" && typeof b === "number") return isFinite(a) && isFinite(b) && Math.abs(a - b) <= tol;
  if (typeof a === "string" && typeof b === "string") return a === b;
  return false;
};
var shownValue = function (value) {
  if (value === null) return "nothing";
  if (typeof value === "string") return "\"" + value + "\"";
  return String(value);
};

var seq = __expect(ARGS.expect);
if (!seq) throw __precondition("the script got no sequence to look in");
if (String(seq.sequenceID) !== String(ARGS.sequence_id)) {
  throw __precondition("the script was sent for sequence " + ARGS.sequence_id + ", but it expects " + seq.sequenceID);
}
var found = __clip(seq, ARGS.target);
if (found.track_type !== "video") throw __precondition("this clip is on an audio track; effect set writes video clips only");
var effect = __effect(found.clip, ARGS.effect, ARGS.effect_index);
var prop = __property(effect.component, ARGS.index, ARGS.name);
if (String(found.clip.nodeId) !== String(ARGS.node_id)) throw __precondition("the clip at this place changed");
if (effect.index !== ARGS.found_effect_index) throw __precondition("the effect at this place changed");
var varying = null;
try { varying = prop.isTimeVarying(); } catch (eVarying) {
  throw __precondition("the animation state of the property could not be read");
}
if (varying) throw __precondition("property is keyframed");
var wanted = ARGS.value;
if (!PLAIN_OK[typeof wanted] || (typeof wanted === "number" && !isFinite(wanted))) {
  throw __precondition("the value must be a number, a string or a boolean");
}
var tolerance = ARGS.tolerance;
if (typeof tolerance !== "number" || !isFinite(tolerance) || tolerance < 0) {
  throw __precondition("the tolerance must be a number of 0 or more");
}

var before = null;
try { before = plain(prop.getValue()); } catch (eBefore) {}
if (ARGS.hasOwnProperty("expect_old") && !sameValue(ARGS.expect_old, before, tolerance)) {
  throw __precondition("the property holds " + shownValue(before) + " now, not " + shownValue(ARGS.expect_old) +
                       " as the plan expects");
}

// From here on, nothing throws.
var zeroFirst = false;
if (typeof before === "number" && before > 0 && typeof wanted === "number" && wanted < 0) {
  zeroFirst = true;
  var zeroError = null;
  var zeroBack = null;
  try { prop.setValue(0, true); } catch (eZero) { zeroError = errorText(eZero); }
  if (zeroError === null) {
    try {
      zeroBack = plain(prop.getValue());
      if (typeof zeroBack !== "number" || Math.abs(zeroBack) > tolerance) zeroError = "read back " + zeroBack;
    } catch (eZeroBack) {
      zeroError = "read back failed: " + errorText(eZeroBack);
    }
  }
  if (zeroError !== null) {
    return __ok({before: before, zero_first: true, set_error: "zero step failed: " + zeroError, value: zeroBack,
                 read_error: null});
  }
}
var setError = null;
try { prop.setValue(wanted, true); } catch (eSet) { setError = errorText(eSet); }
var value = null;
var readError = null;
try { value = plain(prop.getValue()); } catch (eRead) { readError = errorText(eRead); }
return __ok({before: before, zero_first: zeroFirst, set_error: setError, value: value, read_error: readError});
