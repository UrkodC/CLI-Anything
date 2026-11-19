// recipe: place_read
// Reads what live place checks: the sequence, the project items a plan names, and the clips in each shot's span.
// It changes nothing.
// ARGS: {items: [node ID or name], spans: [{entry, track, audio_track, start_ticks, end_ticks}],
//        expect: {project_path, sequence_id, sequence_name or null} (added by the session)}
// Reply: {sequence: {id, name, frame_ticks, video_tracks, audio_tracks},
//         items: [{query, found, node_id, name, matches, has_video, has_audio, still, is_sequence, frame_rate}],
//         spans: [{entry, video: [clip] or null, audio: [clip] or null}],
//         first_v1: {node_id, start_ticks, head_transition} or null}
// A clip: {node_id, item (the node ID of its project item), start_ticks, end_ticks, in_ticks, out_ticks}. Ticks
// cross as strings of digits (null when Premiere gives no whole number).
// - The sequence is the expected one (__expect of find.jsx), never the active one.
// - An item: a node ID match wins. Else its name: matches counts the items with that name; one is taken, two or
//   more are reported (found false, node_id null), not taken. Bins are walked, never matched.
// - has_video, has_audio: the item's span of that stream (out minus in) is above 0; null when unreadable.
// - still: the media path ends in png, jpg, jpeg, tif, tiff, psd, gif, bmp or exr (any case). Measure M9 found
//   no host read that tells a still apart; is_sequence and frame_rate are read for information only.
// - A span lists the clips of video track "track" and audio track "audio_track" that overlap it with half a
//   frame of room: a clip that only touches the span does not count. null when the sequence has no such track.
// - first_v1: the first clip of V1 and whether a transition covers its start (half a frame of room).
//
// Host calls follow the premiere-pro-mcp server (dist/), and find.jsx for the sequence:
// - the sequence by ID with its project check: __expect and __sequenceById of find.jsx
//   (bridge/script-builder.js, lines 554-564);
// - seq.timebase, the frame length in ticks: overwrite_clip (tools/advanced.js, line 1044);
// - seq.videoTracks and seq.audioTracks .numTracks: overwrite_clip (tools/advanced.js, lines 1031-1036);
// - the project item walk: app.project.rootItem, item.children.numItems and [i], item.nodeId, item.name, as
//   __findProjectItem, __childCount and __childAt do (bridge/script-builder.js, lines 926-940 and 951-967);
//   a bin is item.type 2 (__isBinItem, bridge/script-builder.js, lines 894-897);
// - item.getInPoint(type).ticks and item.getOutPoint(type).ticks for type 1 (video) and 2 (audio): the stream
//   spans of overwrite_clip (tools/advanced.js, lines 1049-1055);
// - item.getMediaPath(): tools/advanced.js, line 377; item.isSequence(): tools/media.js, line 780;
//   item.getFootageInterpretation().frameRate: tools/utility.js, line 626;
// - track.clips.numItems and [c], clip.nodeId, clip.projectItem.nodeId: __isPlacedOn of overwrite_clip
//   (tools/advanced.js, lines 1082-1093); clip.start, .end, .inPoint, .outPoint as .ticks: get_full_sequence_info
//   (tools/inspection.js, lines 339-347);
// - track.transitions.numItems and [i], .start.ticks, .end.ticks: tools/transitions.js, lines 13-17, read as
//   clip_list.jsx reads them.

var seq = __expect(ARGS.expect);
if (!seq) throw __precondition("the script got no sequence ID");
if (!ARGS.items || typeof ARGS.items.length !== "number") throw __precondition("items must be a list");
if (!ARGS.spans || typeof ARGS.spans.length !== "number") throw __precondition("spans must be a list");

var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var ticksOf = function (obj, key) {
  try { return __ticks(obj[key]); } catch (eTicks) { return null; }
};

var frameTicks = NaN;
try { frameTicks = seq.timebase ? parseFloat(seq.timebase) : NaN; } catch (eTimebase) {}
var room = (!frameTicks || isNaN(frameTicks)) ? (254016000000 / 24) / 2 : frameTicks / 2;

// ---------- the project items ----------
var isBin = function (item) {
  try { return item.type === 2; } catch (eType) { return false; }
};
var childCount = function (item) {
  try {
    var count = item.children ? item.children.numItems : 0;
    return typeof count === "number" && count > 0 ? count : 0;
  } catch (eCount) {
    return 0;
  }
};
var childAt = function (item, index) {
  try { return item.children[index] || null; } catch (eChild) { return null; }
};
// Every item that is not a bin, in the order __findProjectItem walks them.
var allItems = [];
var walk = function (parent) {
  var count = childCount(parent);
  for (var i = 0; i < count; i++) {
    var child = childAt(parent, i);
    if (!child) continue;
    if (isBin(child)) {
      walk(child);
    } else {
      allItems.push(child);
    }
  }
};
walk(app.project.rootItem);

var streamOf = function (item, kind) {
  try {
    var span = parseFloat(item.getOutPoint(kind).ticks) - parseFloat(item.getInPoint(kind).ticks);
    return isNaN(span) ? null : span > 0;
  } catch (eSpan) {
    return null;
  }
};
var STILL = {png: true, jpg: true, jpeg: true, tif: true, tiff: true, psd: true, gif: true, bmp: true, exr: true};
var stillOf = function (item) {
  try {
    var path = String(item.getMediaPath() || "");
    var dot = path.lastIndexOf(".");
    var slash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
    if (dot < 0 || dot < slash) return false;
    return STILL[path.substring(dot + 1).toLowerCase()] === true;
  } catch (ePath) {
    return null;
  }
};
var sequenceOf = function (item) {
  try { return item.isSequence() === true; } catch (eSeq) { return null; }
};
var rateOf = function (item) {
  try {
    var rate = parseFloat(item.getFootageInterpretation().frameRate);
    return isFinite(rate) && rate > 0 ? rate : null;
  } catch (eRate) {
    return null;
  }
};

var items = [];
for (var q = 0; q < ARGS.items.length; q++) {
  var wanted = String(ARGS.items[q]);
  var byId = null;
  var byName = null;
  var matches = 0;
  for (var k = 0; k < allItems.length; k++) {
    var candidate = allItems[k];
    var id = null;
    var name = null;
    try { id = String(candidate.nodeId); } catch (eId) {}
    try { name = String(candidate.name); } catch (eName) {}
    if (byId === null && id === wanted) byId = candidate;
    if (name === wanted) {
      matches++;
      if (byName === null) byName = candidate;
    }
  }
  var taken = byId !== null ? byId : (matches === 1 ? byName : null);
  var record = {query: wanted, found: taken !== null, node_id: null, name: null, matches: matches, has_video: null,
                has_audio: null, still: null, is_sequence: null, frame_rate: null};
  if (taken !== null) {
    try { record.node_id = text(taken.nodeId); } catch (eTakenId) {}
    try { record.name = text(taken.name); } catch (eTakenName) {}
    record.has_video = streamOf(taken, 1);
    record.has_audio = streamOf(taken, 2);
    record.still = stillOf(taken);
    record.is_sequence = sequenceOf(taken);
    record.frame_rate = rateOf(taken);
  }
  items.push(record);
}

// ---------- the spans ----------
var clipRecord = function (clip) {
  var item = null;
  try { item = clip.projectItem ? text(clip.projectItem.nodeId) : null; } catch (eItem) {}
  var nodeId = null;
  try { nodeId = text(clip.nodeId); } catch (eNode) {}
  return {node_id: nodeId, item: item, start_ticks: ticksOf(clip, "start"), end_ticks: ticksOf(clip, "end"),
          in_ticks: ticksOf(clip, "inPoint"), out_ticks: ticksOf(clip, "outPoint")};
};
var clipsIn = function (tracks, index, from, to) {
  if (!__isIndex(index) || index >= tracks.numTracks) return null;
  var found = [];
  var clips = tracks[index].clips;
  for (var c = 0; c < clips.numItems; c++) {
    var record = clipRecord(clips[c]);
    if (record.start_ticks === null || record.end_ticks === null) {
      found.push(record);                     // a clip whose times cannot be read counts as in the way
      continue;
    }
    var start = parseFloat(record.start_ticks);
    var end = parseFloat(record.end_ticks);
    if (start < to - room && end > from + room) found.push(record);
  }
  return found;
};

var spans = [];
for (var s = 0; s < ARGS.spans.length; s++) {
  var span = ARGS.spans[s];
  var from = parseFloat(span.start_ticks);
  var to = parseFloat(span.end_ticks);
  if (isNaN(from) || isNaN(to)) throw __precondition("span " + s + " has no start or end in ticks");
  spans.push({entry: span.entry, video: clipsIn(seq.videoTracks, span.track, from, to),
              audio: clipsIn(seq.audioTracks, span.audio_track, from, to)});
}

// ---------- the first clip of V1 ----------
var firstV1 = null;
if (seq.videoTracks.numTracks > 0) {
  var v1 = seq.videoTracks[0];
  var first = null;
  var firstStart = NaN;
  for (var f = 0; f < v1.clips.numItems; f++) {
    var at = parseFloat(ticksOf(v1.clips[f], "start"));
    if (!isNaN(at) && (first === null || at < firstStart)) {
      first = v1.clips[f];
      firstStart = at;
    }
  }
  if (first !== null) {
    var head = false;
    try {
      for (var r = 0; r < v1.transitions.numItems; r++) {
        var transition = v1.transitions[r];
        var ts = parseFloat(transition.start.ticks);
        var te = parseFloat(transition.end.ticks);
        if (!isNaN(ts) && !isNaN(te) && ts - room <= firstStart && firstStart <= te + room) head = true;
      }
    } catch (eTransitions) {}
    var firstId = null;
    try { firstId = text(first.nodeId); } catch (eFirstId) {}
    firstV1 = {node_id: firstId, start_ticks: ticksOf(first, "start"), head_transition: head};
  }
}

return __ok({
  sequence: {id: text(seq.sequenceID), name: text(seq.name), frame_ticks: isNaN(frameTicks) ? null : String(frameTicks),
             video_tracks: seq.videoTracks.numTracks, audio_tracks: seq.audioTracks.numTracks},
  items: items,
  spans: spans,
  first_v1: firstV1
});
