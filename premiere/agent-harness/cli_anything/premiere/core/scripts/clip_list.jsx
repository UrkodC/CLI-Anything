// recipe: clip_list
// Reads the clips of one sequence and returns one record per clip, raw fields only. It changes nothing.
// ARGS: {sequence_id: string or null, track_type: "video" or "audio" or "both", track: whole number or null,
//        offset: whole number, limit: whole number of 1 or more, detail: true or false}
// Reply: {sequence: {id, name}, used_active: true or false, total: number, clips: [record]}
// Order: video tracks by index, then audio tracks by index, clips by index. total counts every clip that
// matches track_type and track; clips holds the ones from offset on, at most limit of them. Premiere is
// asked about a clip only when the clip is inside that window, so a long timeline stays cheap.
// A record: track_type, track, clip, node_id, name, start_ticks, end_ticks, in_ticks, out_ticks (strings),
// enabled, speed, adjustment_layer, source {node_id, name, path}, effects (display names in component
// order). With detail: interpreted_fps, linked, keyframed, transitions {head, tail}.
// Every field of a record is read through its own try: when Premiere throws for one of them, that field
// is null and the clip, and the rest of the list, still come.
//
// Host calls follow the premiere-pro-mcp server (dist/), and find.jsx for the sequence:
// - app.project.activeSequence: get_full_sequence_info without a sequence_id (tools/inspection.js,
//   line 258); sequence by ID: __sequenceById of find.jsx (bridge/script-builder.js, lines 554-564);
// - seq.videoTracks and seq.audioTracks .numTracks and [t], track.clips.numItems and [c]: the same
//   tool (tools/inspection.js, lines 324-337 and 401-414);
// - clip.nodeId, clip.name, clip.start/end/inPoint/outPoint .ticks: tools/inspection.js, lines 339-354
//   (the ticks go out as strings through __ticks);
// - enabled: __isClipDisabled (bridge/script-builder.js, lines 777-784): the boolean clip.disabled, then
//   0 or 1, then clip.isDisabled(); when none of them answers the field is null (the server says enabled);
// - clip.getSpeed(), clip.isAdjustmentLayer(): tools/inspection.js, lines 351 and 353;
// - clip.projectItem .nodeId, .name, .getMediaPath(): tools/inspection.js, lines 357-364;
// - clip.components.numItems and [i].displayName: tools/inspection.js, lines 368-373 (no cap of 64 here);
// - interpreted_fps: projectItem.getFootageInterpretation().frameRate (tools/utility.js, line 626);
// - linked: clip.getLinkedItems(), .numItems, [i].nodeId (tools/timeline.js, lines 1418-1423);
// - keyframed: component.properties.numItems and [p], prop.displayName, prop.isTimeVarying()
//   (tools/keyframes.js, lines 90-98; isTimeVarying sits in its own try there too);
// - transitions: track.transitions.numItems and [i], .start.ticks, .end.ticks (tools/transitions.js,
//   lines 13-14); the span that covers the clip's start (head) or end (tail), with half a frame of room, as
//   __newTransitionCovers does (bridge/script-builder.js, lines 2502-2511); the half frame comes from
//   seq.timebase (tools/sequence.js, lines 485-486). The server never reads a transition's name from the
//   track; .name is TrackItem.name of its bundled reference (resources/extendscript-reference.js,
//   resources/extendscript-api-inventory.json) and has not been seen on a real transition: when it is
//   not a non-empty string, the name is "(unnamed)", so that a transition that covers a clip is never
//   lost; head and tail are null only when no transition covers that end of the clip.

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

var wantType = ARGS.track_type;
if (wantType !== "video" && wantType !== "audio" && wantType !== "both") {
  throw __precondition("track_type must be video, audio or both, not " + wantType);
}
if (!__isIndex(ARGS.offset)) throw __precondition("offset must be a whole number of 0 or more, not " + ARGS.offset);
if (!__isIndex(ARGS.limit) || ARGS.limit < 1) throw __precondition("limit must be a whole number of 1 or more, not " + ARGS.limit);
if (__given(ARGS.track) && !__isIndex(ARGS.track)) throw __precondition("track must be a whole number of 0 or more, not " + ARGS.track);

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

var enabledOf = function (clip) {
  try {
    if (typeof clip.disabled === "boolean") return !clip.disabled;
    if (clip.disabled === 1 || clip.disabled === 0) return clip.disabled === 0;
  } catch (eDisabled) {}
  try {
    if (typeof clip.isDisabled === "function") return !clip.isDisabled();
  } catch (eIsDisabled) {}
  return null;
};
var speedOf = function (clip) {
  try { return clip.getSpeed(); } catch (eSpeed) { return null; }
};
var adjustmentOf = function (clip) {
  try { return clip.isAdjustmentLayer(); } catch (eAdjustment) { return null; }
};
var sourceOf = function (clip) {
  var source = {node_id: null, name: null, path: null};
  var item = null;
  try { item = clip.projectItem; } catch (eItem) {}
  if (!item) return source;
  try { source.node_id = text(item.nodeId); } catch (eItemId) {}
  try { source.name = text(item.name); } catch (eItemName) {}
  try {
    var path = item.getMediaPath();
    if (path) source.path = String(path);
  } catch (ePath) {}
  return source;
};
var effectsOf = function (clip) {
  try {
    var names = [];
    var components = clip.components;
    for (var i = 0; i < components.numItems; i++) names.push(text(components[i].displayName));
    return names;
  } catch (eEffects) {
    return null;
  }
};

var interpretedOf = function (clip) {
  try {
    var rate = parseFloat(clip.projectItem.getFootageInterpretation().frameRate);
    return isFinite(rate) && rate > 0 ? rate : null;
  } catch (eRate) {
    return null;
  }
};
var linkedOf = function (clip) {
  try {
    var own = String(clip.nodeId);
    var ids = [];
    var linked = clip.getLinkedItems();
    for (var i = 0; linked && i < linked.numItems; i++) {
      var id = String(linked[i].nodeId);
      if (id !== own) ids.push(id);
    }
    return ids;
  } catch (eLinked) {
    return null;
  }
};
var keyframedOf = function (clip) {
  try {
    var found = [];
    var components = clip.components;
    for (var c = 0; c < components.numItems; c++) {
      var component = components[c];
      var effect = String(component.displayName);
      var props = component.properties;
      for (var p = 0; p < props.numItems; p++) {
        var varying = false;
        try { varying = props[p].isTimeVarying(); } catch (eVarying) {}
        if (varying) found.push(effect + "." + String(props[p].displayName));
      }
    }
    return found;
  } catch (eKeyframed) {
    return null;
  }
};

// Half a frame of room, as the server allows for tick drift; 24 fps when the sequence does not say.
var room = 0;
if (ARGS.detail === true) {
  var frameTicks = NaN;
  try { frameTicks = seq.timebase ? parseFloat(seq.timebase) : NaN; } catch (eTimebase) {}
  if (!frameTicks || isNaN(frameTicks)) frameTicks = 254016000000 / 24;
  room = frameTicks / 2 + 1;
}
// The transitions of a track as [{start, end, name}], or null when the track cannot be read.
var spansOf = function (track) {
  try {
    var spans = [];
    for (var i = 0; i < track.transitions.numItems; i++) {
      var item = track.transitions[i];
      var found = {start: parseFloat(item.start.ticks), end: parseFloat(item.end.ticks), name: "(unnamed)"};
      try {
        if (typeof item.name === "string" && item.name !== "") found.name = item.name;
      } catch (eTransitionName) {}
      spans.push(found);
    }
    return spans;
  } catch (eTransitions) {
    return null;
  }
};
var transitionsOf = function (spans, startTicks, endTicks) {
  if (!spans || startTicks === null || endTicks === null) return null;
  var from = parseFloat(startTicks);
  var to = parseFloat(endTicks);
  var result = {head: null, tail: null};
  var headSeen = false;
  var tailSeen = false;
  for (var i = 0; i < spans.length; i++) {
    var span = spans[i];
    if (isNaN(span.start) || isNaN(span.end)) continue;
    if (!headSeen && span.start - room <= from && from <= span.end + room) {
      headSeen = true;
      result.head = span.name;
    }
    if (!tailSeen && span.start - room <= to && to <= span.end + room) {
      tailSeen = true;
      result.tail = span.name;
    }
  }
  return result;
};

var families = [];
if (wantType === "video" || wantType === "both") families.push(["video", seq.videoTracks]);
if (wantType === "audio" || wantType === "both") families.push(["audio", seq.audioTracks]);

var clips = [];
var total = 0;
var windowEnd = ARGS.offset + ARGS.limit;
for (var f = 0; f < families.length; f++) {
  var tracks = families[f][1];
  for (var t = 0; t < tracks.numTracks; t++) {
    if (__given(ARGS.track) && ARGS.track !== t) continue;
    var track = tracks[t];
    var count = track.clips.numItems;
    var first = Math.max(ARGS.offset - total, 0);
    var last = Math.min(windowEnd - total, count);
    var spans;
    for (var c = first; c < last; c++) {
      var clip = track.clips[c];
      var record = {
        track_type: families[f][0],
        track: t,
        clip: c,
        node_id: textOf(clip, "nodeId"),
        name: textOf(clip, "name"),
        start_ticks: ticksOf(clip, "start"),
        end_ticks: ticksOf(clip, "end"),
        in_ticks: ticksOf(clip, "inPoint"),
        out_ticks: ticksOf(clip, "outPoint"),
        enabled: enabledOf(clip),
        speed: speedOf(clip),
        adjustment_layer: adjustmentOf(clip),
        source: sourceOf(clip),
        effects: effectsOf(clip)
      };
      if (ARGS.detail === true) {
        if (c === first) spans = spansOf(track);
        record.interpreted_fps = interpretedOf(clip);
        record.linked = linkedOf(clip);
        record.keyframed = keyframedOf(clip);
        record.transitions = transitionsOf(spans, record.start_ticks, record.end_ticks);
      }
      clips.push(record);
    }
    total += count;
  }
}

return __ok({
  sequence: {id: text(seq.sequenceID), name: text(seq.name)},
  used_active: usedActive,
  total: total,
  clips: clips
});
