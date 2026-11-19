// recipe: keys_check
// Reads, for every entry of a level plan, what live keys set checks before it writes. It changes nothing.
// ARGS: {entries: [{node_id: string}],
//        expect: {project_path, sequence_id, sequence_name} (added by the write session)}
// Reply: {frame_ticks: string of digits or null (seq.timebase, one frame of the sequence in ticks),
//         entries: [one record per entry, in order]}
// A record: {found, node_id, error, clip, interpreted, item_fps, motion, motion_error}
// - found: false when no clip of the expected sequence has the node ID; error is then the finder's text, and clip,
//   interpreted, item_fps and motion are null. A missing clip does not stop the script: the other entries are read.
// - clip: __clipFacts of keys.jsx (in_ticks, out_ticks, start_ticks, end_ticks, speed, reversed) plus node_id, name,
//   track_type, track and clip (its number on the track).
// - item_fps: getFootageInterpretation().frameRate of the clip's project item as a number, or null when it cannot be
//   read. interpreted (the M6 read): true when item_fps differs from the frame rate of the sequence
//   (254016000000 / frame_ticks) by more than 0.000001, false when it does not, null when either cannot be read.
//   Measure M6 found 23.9760000013456 on an item interpreted to 23.976 and 23.976023976024 on a native one.
// - motion: null when the clip has no single effect named Motion (motion_error says why), else
//   {effect_index, uniform (Uniform Scale on), scale, rotation}. scale is the property at index 1:
//   {index: 1, name, matches (it is "Scale" by the rule of __propertyNameMatches), value, keyframed}, or null when
//   Motion has no index 1. rotation is the property at index 4: {index: 4, name, keyframed, keys, value}, or null.
//   keyframed is prop.isTimeVarying(), null when it throws. keys and value are __readKeys of keys.jsx (keys is
//   null when the read refuses). value of scale is __readValue of values.jsx.
// Needs find.jsx, values.jsx and keys.jsx before it (the CLI builds it with all three).
// The sequence: __expect of find.jsx, so a renamed sequence or another project refuses the whole read.
//
// Host calls, each a call of premiere-pro-mcp 1.25.0 (dist/):
// - the project, the sequence and the clip: __expect and __clip of find.jsx (bridge/script-builder.js, lines
//   554-564 and 1070-1098, line 3322 for app.project.path);
// - seq.timebase: tools/sequence.js, lines 485-486 (and tools/advanced.js, line 1044);
// - clip.nodeId and clip.name: tools/inspection.js, lines 339-354; the clip times, speed and reverse state:
//   __clipFacts of keys.jsx;
// - clip.projectItem: tools/inspection.js, line 358; item.getFootageInterpretation().frameRate:
//   tools/source-freeze-frame.js, line 217 (parseFloat of it, in a try) and tools/media.js, lines 451 and 629;
// - the effect: __effect of find.jsx (tools/keyframes.js, lines 182-186); component.properties.numItems and [i],
//   prop.displayName: __resolveProperty (bridge/script-builder.js, lines 724-733);
// - Uniform Scale and the name rule: __isUniformScale and __propertyNameMatches of keys.jsx;
// - prop.isTimeVarying(): tools/keyframes.js, line 320 (a throw gives null here);
// - the keys and values: __readKeys of keys.jsx, __readValue of values.jsx.

var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var textOf = function (obj, key) {
  try { return text(obj[key]); } catch (eText) { return null; }
};
var errorText = function (e) {
  var said = "";
  try { said = String(e && e.message !== undefined ? e.message : e); } catch (eSaid) { said = "an error"; }
  return said.replace(/^precondition: /, "");
};
var varyingOf = function (prop) {
  try { return !!prop.isTimeVarying(); } catch (eVarying) { return null; }
};

if (!ARGS.entries || typeof ARGS.entries !== "object" || typeof ARGS.entries.length !== "number") {
  throw __precondition("the script got no list of entries");
}
var seq = __expect(ARGS.expect);
if (!seq) throw __precondition("the script got no sequence ID");

var frameTicks = null;
try {
  var base = String(seq.timebase);
  if (/^[0-9]+$/.test(base)) frameTicks = base;
} catch (eBase) {}

var sequenceFps = frameTicks === null || !(parseFloat(frameTicks) > 0) ? null : 254016000000 / parseFloat(frameTicks);

var readMotion = function (clip) {
  var located = __effect(clip, "Motion", null);
  var component = located.component;
  var props = component.properties;
  var out = {effect_index: located.index, uniform: __isUniformScale(component), scale: null, rotation: null};
  if (props.numItems > 1) {
    var scale = props[1];
    var scaleName = textOf(scale, "displayName");
    out.scale = {index: 1, name: scaleName,
                 matches: scaleName !== null && __propertyNameMatches(scaleName, "Scale", component),
                 value: __readValue(scale).value, keyframed: varyingOf(scale)};
  }
  if (props.numItems > 4) {
    var rotation = props[4];
    var read = null;
    try { read = __readKeys(rotation); } catch (eRead) { read = null; }
    out.rotation = {index: 4, name: textOf(rotation, "displayName"), keyframed: varyingOf(rotation),
                    keys: read ? read.keys : null, value: read ? read.value : null};
  }
  return out;
};

var records = [];
for (var n = 0; n < ARGS.entries.length; n++) {
  var nodeId = ARGS.entries[n] ? text(ARGS.entries[n].node_id) : null;
  var record = {found: false, node_id: nodeId, error: null, clip: null, interpreted: null, item_fps: null, motion: null,
                motion_error: null};
  var found = null;
  try {
    found = __clip(seq, {node_id: nodeId});
  } catch (eFind) {
    record.error = errorText(eFind);
    records.push(record);
    continue;
  }
  var clip = found.clip;
  record.found = true;
  var facts = __clipFacts(clip);
  facts.node_id = textOf(clip, "nodeId");
  facts.name = textOf(clip, "name");
  facts.track_type = found.track_type;
  facts.track = found.track;
  facts.clip = found.index;
  record.clip = facts;
  try {
    var rate = parseFloat(clip.projectItem.getFootageInterpretation().frameRate);
    if (rate > 0 && rate <= 1000) record.item_fps = rate;
  } catch (eRate) {}
  if (record.item_fps !== null && sequenceFps !== null) {
    record.interpreted = Math.abs(record.item_fps - sequenceFps) > 0.000001;
  }
  try {
    record.motion = readMotion(clip);
  } catch (eMotion) {
    record.motion = null;
    record.motion_error = errorText(eMotion);
  }
  records.push(record);
}

return __ok({frame_ticks: frameTicks, entries: records});
