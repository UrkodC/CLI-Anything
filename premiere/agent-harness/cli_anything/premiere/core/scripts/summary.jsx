// Read-only counts for the active sequence. It never lists clips.
// Host calls follow the premiere-pro-mcp server: get_premiere_state (dist/tools/scripting.js) for the
// project name, the active sequence id and name and the track counts, and get_total_clip_count
// (dist/tools/utility.js) for the clip counts.
var text = function (value) {
  return value === undefined || value === null ? null : String(value);
};
var tally = function (tracks) {
  var clips = 0;
  for (var t = 0; t < tracks.numTracks; t++) clips += tracks[t].clips.numItems;
  return {tracks: tracks.numTracks, clips: clips};
};

var project = null;
try { project = app.project; } catch (eProject) {}
var sequence = null;
try { sequence = project ? project.activeSequence : null; } catch (eSequence) {}

var video = sequence ? tally(sequence.videoTracks) : {tracks: 0, clips: 0};
var audio = sequence ? tally(sequence.audioTracks) : {tracks: 0, clips: 0};

return __ok({
  project: project ? text(project.name) : null,
  active_sequence: sequence ? {id: text(sequence.sequenceID), name: text(sequence.name)} : null,
  video_tracks: video.tracks,
  audio_tracks: audio.tracks,
  video_clips: video.clips,
  audio_clips: audio.clips
});
