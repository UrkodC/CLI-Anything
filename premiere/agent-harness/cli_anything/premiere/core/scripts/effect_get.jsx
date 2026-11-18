// recipe: effect_get
// Reads the effects of one clip and the properties of each effect, by index. It changes nothing.
// ARGS: {sequence_id: string or null,
//        target: {track_type: "video" or "audio", track: whole number, clip: whole number} or {node_id: string},
//        effect: string or null, effect_index: whole number or null}
// Reply: {sequence: {id, name}, used_active: true or false,
//         clip: {node_id, name, track_type, track, clip, start_ticks},
//         effects: [{effect_index, name, match_name, properties: [property] or null}]}
// A property: {index, name, value, kind, keyframed}. kind is "number", "string", "boolean", "color" (value is
// [alpha, red, green, blue]), "array" (value is an array of finite numbers, such as Position) or "unreadable"
// (value is null: a read that gave nothing or threw, NaN, an infinity, a number of 4294967296 or more that is not a
// colour, or anything else). Names are never trimmed: Lumetri Color has names of one space and empty names.
// Which effects: without effect and effect_index, every component of the clip in component order. With
// effect, the components whose display name or match name is exactly that (all of them when the name occurs
// twice: this is a read). With effect_index, the component at that index of clip.components. With both, the
// component at that index when its name matches (refused with a precondition text when it does not).
// Every read of a property sits in its own try: when Premiere throws for one of them, that field is null (or
// the kind is "unreadable") and the clip, and the other properties, still come. An effect whose property
// list cannot be read has properties null.
//
// Host calls follow the premiere-pro-mcp server (dist/), and find.jsx for the sequence, the clip and the
// effect:
// - app.project.activeSequence: get_full_sequence_info without a sequence_id (tools/inspection.js,
//   line 258); sequence by ID: __sequenceById of find.jsx (bridge/script-builder.js, lines 554-564);
// - the clip: __clip of find.jsx (bridge/script-builder.js, lines 1070-1098 and tools/inspection.js,
//   lines 324-337 and 401-414); clip.nodeId, clip.name, clip.start.ticks (tools/inspection.js,
//   lines 339-354; the ticks go out as a string through __ticks);
// - clip.components.numItems and [i], .displayName, .matchName: tools/keyframes.js, lines 182-186
//   (get_effect_properties matches by display name or match name the same way, lines 80-82);
// - component.properties.numItems and [p], prop.displayName: tools/keyframes.js, lines 90-95;
// - prop.isTimeVarying(): tools/keyframes.js, line 98, in its own try;
// - the value, in the order of get_effect_properties (tools/keyframes.js, lines 100-110): first the colour,
//   as __readColorValue does (bridge/script-builder.js, lines 662-674): prop.getColorValue() with four
//   finite channels gives [alpha, red, green, blue]; it throws on a parameter that is not a colour. Then
//   prop.getValue(0, 0), as __readableParamValue gets it (bridge/script-builder.js, lines 2049-2059); that
//   function asks getColorValue a second time for a number of 4294967296 or more, which is the same question
//   as the first one, so it is not asked again here. Such a number is a colour packed into one 64-bit integer,
//   which a double cannot hold exactly (bridge/script-builder.js, lines 2045-2048). With no colour read behind
//   it the number is lossy and must not be written back, so the value is "unreadable" and null.
// - prop.areKeyframesSupported() is not read: the record has no field for it.

var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
// A text or a tick string read from obj[key]; null when the host throws for it.
var textOf = function (obj, key) {
  try { return text(obj[key]); } catch (eText) { return null; }
};
var ticksOf = function (obj, key) {
  try { return __ticks(obj[key]); } catch (eTicks) { return null; }
};

if (__given(ARGS.effect) && typeof ARGS.effect !== "string") throw __precondition("effect must be a text, not " + ARGS.effect);

var seq = null;
var usedActive = false;
if (__given(ARGS.sequence_id)) {
  seq = __sequenceById(ARGS.sequence_id);
  if (!seq) throw __precondition("no sequence has the ID " + ARGS.sequence_id);
} else {
  usedActive = true;
  seq = app.project ? app.project.activeSequence : null;
  if (!seq) throw __precondition("no sequence is active; pass --sequence");
}

var found = __clip(seq, ARGS.target);
var clip = found.clip;
var components = clip.components;

// The indexes in clip.components of the effects to read.
var nameIs = function (component, wanted) {
  return textOf(component, "displayName") === wanted || textOf(component, "matchName") === wanted;
};
var picked = [];
if (__given(ARGS.effect_index)) {
  if (__given(ARGS.effect)) {
    picked.push(__effect(clip, ARGS.effect, ARGS.effect_index).index);
  } else {
    if (!__isIndex(ARGS.effect_index)) {
      throw __precondition("effect_index must be a whole number of 0 or more, not " + ARGS.effect_index);
    }
    if (ARGS.effect_index >= components.numItems) throw __precondition("this clip has no effect at index " + ARGS.effect_index);
    picked.push(ARGS.effect_index);
  }
} else if (__given(ARGS.effect)) {
  for (var i = 0; i < components.numItems; i++) {
    if (nameIs(components[i], ARGS.effect)) picked.push(i);
  }
  if (picked.length === 0) throw __precondition("no effect named " + ARGS.effect + " on this clip");
} else {
  for (var all = 0; all < components.numItems; all++) picked.push(all);
}

// The colour read of __readColorValue: [alpha, red, green, blue], or null for anything that is not a colour.
var readColor = function (prop) {
  if (!prop || typeof prop.getColorValue !== "function") return null;
  var color;
  try { color = prop.getColorValue(); } catch (eColor) { return null; }
  if (!color || color.length !== 4) return null;
  var out = [];
  for (var k = 0; k < 4; k++) {
    var channel = Number(color[k]);
    if (!isFinite(channel)) return null;
    out.push(channel);
  }
  return out;
};
// {kind, value} for one property. The colour first, then getValue(0, 0), then the type of what it gave.
var readValue = function (prop) {
  try {
    var color = readColor(prop);
    if (color) return {kind: "color", value: color};
    var raw = prop.getValue(0, 0);
    var type = typeof raw;
    if (type === "number") {
      return isFinite(raw) && raw < 4294967296 ? {kind: "number", value: raw} : {kind: "unreadable", value: null};
    }
    if (type === "string") return {kind: "string", value: raw};
    if (type === "boolean") return {kind: "boolean", value: raw};
    if (raw !== null && type === "object" && Object.prototype.toString.call(raw) === "[object Array]") {
      var items = [];
      for (var n = 0; n < raw.length; n++) {
        if (typeof raw[n] !== "number" || !isFinite(raw[n])) return {kind: "unreadable", value: null};
        items.push(raw[n]);
      }
      return {kind: "array", value: items};
    }
  } catch (eRead) {}
  return {kind: "unreadable", value: null};
};
var propertyOf = function (props, p) {
  var prop = null;
  try { prop = props[p]; } catch (eProp) {}
  if (!prop) return {index: p, name: null, value: null, kind: "unreadable", keyframed: null};
  var read = readValue(prop);
  var keyframed = null;
  try { keyframed = !!prop.isTimeVarying(); } catch (eVarying) {}
  return {index: p, name: textOf(prop, "displayName"), value: read.value, kind: read.kind, keyframed: keyframed};
};
var propertiesOf = function (component) {
  try {
    var props = component.properties;
    var list = [];
    for (var p = 0; p < props.numItems; p++) list.push(propertyOf(props, p));
    return list;
  } catch (eProps) {
    return null;
  }
};

var listed = [];
for (var e = 0; e < picked.length; e++) {
  var component = components[picked[e]];
  listed.push({
    effect_index: picked[e],
    name: textOf(component, "displayName"),
    match_name: textOf(component, "matchName"),
    properties: propertiesOf(component)
  });
}

return __ok({
  sequence: {id: text(seq.sequenceID), name: text(seq.name)},
  used_active: usedActive,
  clip: {
    node_id: textOf(clip, "nodeId"),
    name: textOf(clip, "name"),
    track_type: found.track_type,
    track: found.track,
    clip: found.index,
    start_ticks: ticksOf(clip, "start")
  },
  effects: listed
});
