// recipe: effect_resolve
// Finds the target of every entry of an effect plan, in one read, before anything is written. It changes nothing.
// ARGS: {sequence_id: string,
//        entries: [{target: {track_type: "video", track, clip} or {node_id}, effect: string,
//                   effect_index: whole number or null, index: whole number, name: string}],
//        expect: {project_path, sequence_id, sequence_name} (added by the write session)}
// Reply: {sequence: {id, name},
//         entries: [{clip: {node_id, name, track_type, track, clip, start_ticks, end_ticks (only when readable)},
//                    effect: {effect_index, name, match_name},
//                    property: {index, name, value, kind, keyframed}}]}, one record per entry, in order.
// The first entry that cannot be found makes the whole script fail, so nothing is half resolved: a finder's
// refusal comes back as "precondition: entry <n>: <the finder's text>" (n counts from 0); a host call that
// throws comes back as "entry <n>: <the error>". A clip found on an audio track (a node ID can name one) is
// refused: effect set writes video clips only. value and kind come from __readValue of values.jsx, as in
// effect_get.jsx. keyframed is prop.isTimeVarying() in its own try, null when it throws. end_ticks (the end of the
// clip in the sequence, for live lut set --prove) is read as clip_list.jsx reads it; the key is left out when the
// end cannot be read, so a reader takes a missing key as unreadable.
//
// Host calls follow the premiere-pro-mcp server (dist/), through find.jsx and values.jsx:
// - the project and the sequence: __expect of find.jsx (app.project.path: bridge/script-builder.js, line 3322;
//   the sequence by ID: lines 554-564);
// - the clip: __clip of find.jsx (bridge/script-builder.js, lines 1070-1098); clip.nodeId, clip.name and
//   clip.start.ticks and clip.end.ticks (tools/inspection.js, lines 339-354, the ticks as a string through __ticks);
// - the effect: __effect of find.jsx (tools/keyframes.js, lines 182-186), component.displayName, .matchName;
// - the property: __property of find.jsx (bridge/script-builder.js, lines 724-733);
// - prop.isTimeVarying(): tools/keyframes.js, line 98 (read) and lines 215-218 (the write refusal);
// - the value: __readValue of values.jsx (tools/keyframes.js, lines 100-110).

var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var textOf = function (obj, key) {
  try { return text(obj[key]); } catch (eText) { return null; }
};
var ticksOf = function (obj, key) {
  try { return __ticks(obj[key]); } catch (eTicks) { return null; }
};
var PREFIX = "precondition: ";
// The error of entry n: a finder's refusal keeps its "precondition: " in front, anything else does not get it.
var entryError = function (n, e) {
  var message = e && typeof e.message === "string" ? e.message : String(e);
  if (message.substring(0, PREFIX.length) === PREFIX) {
    return __precondition("entry " + n + ": " + message.substring(PREFIX.length));
  }
  return new Error("entry " + n + ": " + String(e));
};

var seq = __expect(ARGS.expect);
if (!seq) throw __precondition("the script got no sequence to look in");
if (String(seq.sequenceID) !== String(ARGS.sequence_id)) {
  throw __precondition("the script was sent for sequence " + ARGS.sequence_id + ", but it expects " + seq.sequenceID);
}
if (!ARGS.entries || typeof ARGS.entries.length !== "number") throw __precondition("the script got no entries");

var records = [];
for (var n = 0; n < ARGS.entries.length; n++) {
  var entry = ARGS.entries[n];
  var record;
  try {
    var found = __clip(seq, entry.target);
    if (found.track_type !== "video") throw __precondition("this clip is on an audio track; effect set writes video clips only");
    var effect = __effect(found.clip, entry.effect, entry.effect_index);
    var prop = __property(effect.component, entry.index, entry.name);
    var read = __readValue(prop);
    var keyframed = null;
    try { keyframed = !!prop.isTimeVarying(); } catch (eVarying) {}
    record = {
      clip: {node_id: textOf(found.clip, "nodeId"), name: textOf(found.clip, "name"), track_type: found.track_type,
             track: found.track,
             clip: found.index, start_ticks: ticksOf(found.clip, "start")},
      effect: {effect_index: effect.index, name: textOf(effect.component, "displayName"),
               match_name: textOf(effect.component, "matchName")},
      property: {index: entry.index, name: textOf(prop, "displayName"), value: read.value, kind: read.kind,
                 keyframed: keyframed}
    };
    var endTicks = ticksOf(found.clip, "end");
    if (endTicks !== null) record.clip.end_ticks = endTicks;
  } catch (e) {
    throw entryError(n, e);
  }
  records.push(record);
}

return __ok({sequence: {id: text(seq.sequenceID), name: text(seq.name)}, entries: records});
