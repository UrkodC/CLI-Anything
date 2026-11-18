// Read-only snapshot of Premiere: version, open projects, active sequence, playhead and selection.
// Host calls follow the premiere-pro-mcp server: get_premiere_state (dist/tools/scripting.js) for the
// version, build, active sequence, playhead and selection; __openProjects and __normProjectPath
// (dist/bridge/script-builder.js) for the open projects; __findClip (same file) for where a
// selected clip sits (track type, track index, clip index).
var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var normPath = function (path) {
  return String(path || "").split(String.fromCharCode(92)).join("/").toLowerCase();
};
var sameProject = function (a, b) {
  try {
    if (!a || !b) return false;
    if (a.documentID && b.documentID) return String(a.documentID) === String(b.documentID);
    return normPath(a.path) !== "" && normPath(a.path) === normPath(b.path);
  } catch (eSame) {
    return false;
  }
};

var version = null;
var build = null;
try { version = text(app.version); } catch (eVersion) {}
try { build = text(app.build); } catch (eBuild) {}

var activeProject = null;
try { activeProject = app.project; } catch (eProject) {}
var open = [];
try {
  for (var i = 0; i < app.projects.numProjects; i++) open.push(app.projects[i]);
} catch (eProjects) {
  open = activeProject ? [activeProject] : [];
}
var projects = [];
for (var p = 0; p < open.length; p++) {
  projects.push({name: text(open[p].name), path: text(open[p].path), active: sameProject(open[p], activeProject)});
}

var sequence = null;
try { sequence = activeProject ? activeProject.activeSequence : null; } catch (eSequence) {}
var activeSequence = null;
var playhead = null;
var selection = [];
if (sequence) {
  activeSequence = {id: text(sequence.sequenceID), name: text(sequence.name)};
  try {
    var ticks = String(sequence.getPlayerPosition().ticks);
    if (/^[0-9]+$/.test(ticks)) playhead = ticks;
  } catch (ePlayhead) {}
  try {
    var picked = sequence.getSelection();
    if (picked && picked.length) {
      // One pass over the timeline: where each clip sits, keyed by its node id.
      var where = {};
      var families = [["video", sequence.videoTracks], ["audio", sequence.audioTracks]];
      for (var f = 0; f < families.length; f++) {
        var tracks = families[f][1];
        for (var t = 0; t < tracks.numTracks; t++) {
          var clips = tracks[t].clips;
          for (var c = 0; c < clips.numItems; c++) {
            where["$" + String(clips[c].nodeId)] = {type: families[f][0], track: t, clip: c};
          }
        }
      }
      for (var s = 0; s < picked.length; s++) {
        var found = where["$" + String(picked[s].nodeId)];
        selection.push({
          track_type: found ? found.type : null,
          track: found ? found.track : null,
          clip: found ? found.clip : null,
          node_id: text(picked[s].nodeId),
          name: text(picked[s].name)
        });
      }
    }
  } catch (eSelection) {
    selection = [];
  }
}

return __ok({
  version: version,
  build: build,
  projects: projects,
  active_sequence: activeSequence,
  playhead_ticks: playhead,
  selection: selection
});
