// find.jsx: target finders for recipe scripts
// Function definitions only: nothing here runs when the script loads. ES3 only.
// Every refusal throws an Error whose message starts with "precondition: ", before any edit; the catch
// line of jsx.build turns it into a failure reply and the CLI reports it with the code "precondition".
// A write body calls __expect(ARGS.expect) before its first other host call.
// Host calls follow the premiere-pro-mcp server (dist/) and tests/gates/run_gates.py:
// - app.project and app.project.path: the expected project guard (bridge/script-builder.js, line 3322);
// - project.sequences.numSequences, project.sequences[i], seq.sequenceID, seq.name: __findSequence
//   (bridge/script-builder.js, lines 554-564) and FIND_PROPERTY_JS of the gates;
// - videoTracks/audioTracks .numTracks and [t], track.clips.numItems and [c], clip.nodeId: __findClip
//   (bridge/script-builder.js, lines 1070-1098), walked here in the given sequence, never the active one;
// - clip.components.numItems and [i], .displayName, .matchName: set_effect_property (tools/keyframes.js,
//   lines 182-186);
// - component.properties.numItems and [p], prop.displayName: __resolveProperty with an index
//   (bridge/script-builder.js, lines 724-733) and FIND_PROPERTY_JS of the gates;
// - time.ticks: clip times in get_full_sequence_info (tools/inspection.js, lines 339-354).

function __precondition(text) {
  return new Error("precondition: " + text);
}

function __isIndex(value) {
  return typeof value === "number" && isFinite(value) && Math.floor(value) === value && value >= 0;
}

function __given(value) {
  return value !== null && value !== undefined;
}

// The sequence of the active project with this ID, or null.
function __sequenceById(id) {
  var project = app.project;
  if (!project) return null;
  var wanted = String(id);
  for (var i = 0; i < project.sequences.numSequences; i++) {
    var seq = project.sequences[i];
    if (String(seq.sequenceID) === wanted) return seq;
  }
  return null;
}

// expect: {project_path, sequence_id or null, sequence_name or null}. Checks that the active project
// is the expected one (plain string comparison of its path) and, with a sequence_id, that the sequence
// exists and still has the expected name. Returns that sequence, or null without a sequence_id.
function __expect(expect) {
  if (!expect || typeof expect !== "object") throw __precondition("the script got no expectation");
  var path = app.project && app.project.path;
  if (!path) throw __precondition("no saved project is active");
  if (String(path) !== String(expect.project_path)) {
    throw __precondition("the active project is " + path + ", not " + expect.project_path);
  }
  if (!__given(expect.sequence_id)) return null;
  var seq = __sequenceById(expect.sequence_id);
  if (!seq) throw __precondition("no sequence has the ID " + expect.sequence_id);
  if (__given(expect.sequence_name) && String(seq.name) !== String(expect.sequence_name)) {
    throw __precondition("sequence " + expect.sequence_id + " is named " + seq.name + ", not " + expect.sequence_name);
  }
  return seq;
}

// target: {track_type: "video" or "audio" (default "video"), track, clip} or {node_id}. A node ID is
// looked for in the video tracks, then the audio tracks, of seq only.
// Returns {clip, track_type, track, index}.
function __clip(seq, target) {
  if (!seq) throw __precondition("there is no sequence to look in");
  if (!target || typeof target !== "object") throw __precondition("the script got no clip target");
  if (__given(target.node_id)) {
    var wanted = String(target.node_id);
    var families = [["video", seq.videoTracks], ["audio", seq.audioTracks]];
    for (var f = 0; f < families.length; f++) {
      var group = families[f][1];
      for (var t = 0; t < group.numTracks; t++) {
        var clips = group[t].clips;
        for (var c = 0; c < clips.numItems; c++) {
          if (String(clips[c].nodeId) === wanted) {
            return {clip: clips[c], track_type: families[f][0], track: t, index: c};
          }
        }
      }
    }
    throw __precondition("no clip has the node ID " + wanted + " in sequence " + seq.name);
  }
  var type = __given(target.track_type) ? target.track_type : "video";
  if (type !== "video" && type !== "audio") throw __precondition("track_type must be video or audio, not " + type);
  if (!__isIndex(target.track)) throw __precondition("track must be a whole number of 0 or more, not " + target.track);
  if (!__isIndex(target.clip)) throw __precondition("clip must be a whole number of 0 or more, not " + target.clip);
  var tracks = type === "video" ? seq.videoTracks : seq.audioTracks;
  if (target.track >= tracks.numTracks) throw __precondition("the sequence has no " + type + " track " + target.track);
  var track = tracks[target.track];
  if (target.clip >= track.clips.numItems) {
    throw __precondition(type + " track " + target.track + " has no clip " + target.clip);
  }
  return {clip: track.clips[target.clip], track_type: type, track: target.track, index: target.clip};
}

// name matches an effect's display name or match name exactly. effectIndex (a number, or null) is the
// index in clip.components: the effect there must match name. Without it exactly one effect may match.
// Returns {component, index}.
function __effect(clip, name, effectIndex) {
  var components = clip.components;
  var wanted = String(name);
  if (__given(effectIndex)) {
    if (!__isIndex(effectIndex)) throw __precondition("effect_index must be a whole number of 0 or more, not " + effectIndex);
    if (effectIndex >= components.numItems) throw __precondition("this clip has no effect at index " + effectIndex);
    var at = components[effectIndex];
    if (String(at.displayName) !== wanted && String(at.matchName) !== wanted) {
      throw __precondition("effect " + effectIndex + " of this clip is " + at.displayName + ", not " + wanted);
    }
    return {component: at, index: effectIndex};
  }
  var found = null;
  var count = 0;
  for (var i = 0; i < components.numItems; i++) {
    var candidate = components[i];
    if (String(candidate.displayName) === wanted || String(candidate.matchName) === wanted) {
      if (!found) found = {component: candidate, index: i};
      count++;
    }
  }
  if (count === 0) throw __precondition("no effect named " + wanted + " on this clip");
  if (count > 1) throw __precondition("ambiguous_effect: " + count + " effects are named " + wanted + "; pass effect_index");
  return found;
}

// The property at index of component, when its display name is exactly name (no trimming: Lumetri has
// names of one space and empty names). Returns the property.
function __property(component, index, name) {
  var effect = String(component.displayName);
  if (!__isIndex(index)) throw __precondition("property index must be a whole number of 0 or more, not " + index);
  if (index >= component.properties.numItems) throw __precondition(effect + " has no property " + index);
  var prop = component.properties[index];
  var shown = String(prop.displayName);
  if (shown !== String(name)) {
    throw __precondition("property " + index + " of " + effect + " is named " + shown + ", not " + name);
  }
  return prop;
}

// The ticks of a Premiere time as a string of digits (ticks pass 2^53, so they never cross as numbers),
// or null when there is no time or its ticks are not a whole number.
function __ticks(time) {
  if (!__given(time)) return null;
  var ticks = String(time.ticks);
  return /^-?[0-9]+$/.test(ticks) ? ticks : null;
}
