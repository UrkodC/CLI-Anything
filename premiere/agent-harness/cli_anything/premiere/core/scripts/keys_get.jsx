// recipe: keys_get
// Reads the keys of one property of one clip, in the sequence with the given ID. It changes nothing.
// ARGS: {sequence_id: string,
//        target: {track_type: "video" or "audio", track: whole number, clip: whole number} or {node_id: string},
//        effect: string, property: string, property_index: whole number or null}
// Reply: {sequence: {id, name},
//         clip: {in_ticks, out_ticks, start_ticks, end_ticks, speed, reversed, node_id, name, track_type, track, clip},
//         effect: {name, effect_index},
//         property: {name, index},
//         read: {is_time_varying, keys: [{ticks, value}], value}}
// The ticks are strings of digits (or null when the clip will not say). The key ticks are media ticks. See keys.jsx
// for the read of the clip and of the keys, and for the host calls with the lines of the server that they follow.
// Which clip: __clip of find.jsx in the given sequence, never the active one, and no active-sequence fallback.
// Which effect: __effect of find.jsx without an index, so exactly one effect may have the name (display name or
// match name). Which property: with property_index, __property of find.jsx (the name at that index must match);
// without it, __propertyNamed of keys.jsx (exactly one property may have the name).
// Needs find.jsx, values.jsx and keys.jsx before it (the CLI builds it with all three).
// Host calls: the ones of the clip, the keys and the value are in find.jsx, values.jsx and keys.jsx. This body
// itself reads: seq.sequenceID and seq.name (bridge/script-builder.js, lines 554-564; no try, the sequence was
// found by its ID just before); clip.nodeId and clip.name (tools/inspection.js, lines 339-354); the displayName of
// the effect and of the property (tools/keyframes.js, lines 182-186 and 90-95). The last three sit in a try each.

var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var textOf = function (obj, key) {
  try { return text(obj[key]); } catch (eText) { return null; }
};

if (!__given(ARGS.sequence_id)) throw __precondition("the script got no sequence ID");
if (typeof ARGS.effect !== "string" || ARGS.effect === "") throw __precondition("effect must be a text, not " + ARGS.effect);
if (typeof ARGS.property !== "string") throw __precondition("property must be a text, not " + ARGS.property);

var seq = __sequenceById(ARGS.sequence_id);
if (!seq) throw __precondition("no sequence has the ID " + ARGS.sequence_id);

var found = __clip(seq, ARGS.target);
var clip = found.clip;
var located = __effect(clip, ARGS.effect, null);

var prop = null;
var propIndex = null;
if (__given(ARGS.property_index)) {
  prop = __property(located.component, ARGS.property_index, ARGS.property);
  propIndex = ARGS.property_index;
} else {
  var named = __propertyNamed(located.component, ARGS.property);
  prop = named.property;
  propIndex = named.index;
}

var facts = __clipFacts(clip);
facts.node_id = textOf(clip, "nodeId");
facts.name = textOf(clip, "name");
facts.track_type = found.track_type;
facts.track = found.track;
facts.clip = found.index;

var read = __readKeys(prop);

return __ok({
  sequence: {id: text(seq.sequenceID), name: text(seq.name)},
  clip: facts,
  effect: {name: textOf(located.component, "displayName"), effect_index: located.index},
  property: {name: textOf(prop, "displayName"), index: propIndex},
  read: read
});
