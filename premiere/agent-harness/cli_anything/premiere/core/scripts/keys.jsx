// keys.jsx: the key helpers of the keys scripts
// Function definitions only: nothing here runs when the script loads. ES3 only. It needs find.jsx (__precondition,
// __given, __ticks) and values.jsx (__readColor, __readValue) in front of it.
// Every refusal throws an Error whose message starts with "precondition: ", as in find.jsx.
// Key times are media time: the clip's in point plus the time from the clip start. Python does the tick sums
// (integers); this file only reads and writes tick strings. A tick value never crosses as a number.
// Host calls follow the premiere-pro-mcp server (dist/):
// - new Time() and time.ticks = String(ticks): __clipKeyTime (bridge/script-builder.js, lines 1983-1987);
// - prop.isTimeVarying(): get_keyframes (tools/keyframes.js, line 320). The server swallows a throw there and goes
//   on as if the property had no keys; this file refuses instead, because a read must not report a static value
//   for a property that may have keys;
// - the colour of a keyframed property: __readColor of values.jsx, which is __readColorValue (bridge/script-builder.js,
//   lines 662-674), asked after isTimeVarying as get_keyframes asks it (tools/keyframes.js, line 325): keyframed
//   colours are refused;
// - prop.getKeys(): tools/keyframes.js, line 329. A list of 0 or undefined is an empty list, as __isEmptyKeyList
//   reads it (bridge/script-builder.js, lines 1994-2003); a key time is read as the server does (key.ticks);
// - prop.getValueAtKey(time) with the key as given by getKeys: tools/keyframes.js, line 335, in its own try;
// - the value of a property with no keys: __readValue of values.jsx, that is prop.getValue(0, 0)
//   (bridge/script-builder.js, lines 2049-2059);
// - clip.getSpeed() and clip.isSpeedReversed(): __clipKeyframeBase (bridge/script-builder.js, lines 1922-1943; the
//   speed at line 1926, the reverse state at line 1928, and the same readings of the state at lines 1935-1937);
// - clip.inPoint, clip.start and clip.end ticks: __clipKeyframeBase (bridge/script-builder.js, lines 1938-1939);
//   clip.outPoint ticks: tools/inspection.js, lines 339-354;
// - component.properties.numItems and [i].displayName: __resolveProperty (bridge/script-builder.js, lines 724-745);
// - the name rule of __propertyNamed: __propertyNameMatches (bridge/script-builder.js, lines 694-699): "Scale" and
//   "Scale Height" are one property while the component's Uniform Scale is on. Uniform Scale is read as
//   __isUniformScale reads it (bridge/script-builder.js, lines 643-656): the property named "Uniform Scale" (or its
//   es-ES label "Escala uniforme", __videoIntrinsicPropertyMatches, lines 679-692), prop.getValue() true or 1, a throw
//   counts as off;
// - the list of names in a refusal: __availablePropertyNames (bridge/script-builder.js, lines 701-722).
// Nothing in this file changes Premiere. The writes are in keys_write.jsx, with their own citations.

// A Premiere Time that holds ticks. ticks is a string of digits (or a number small enough to print as digits).
function __keyTime(ticks) {
  var time = new Time();
  time.ticks = String(ticks);
  return time;
}

// The ticks of obj[key] as a string of digits, or null when there is no time, the ticks are not digits, or the
// host throws.
function __timeTicks(obj, key) {
  try { return __ticks(obj[key]); } catch (eTicks) { return null; }
}

// True when the component's Uniform Scale is on (__isUniformScale, bridge/script-builder.js, lines 643-656).
function __isUniformScale(component) {
  if (!component || !component.properties) return false;
  for (var i = 0; i < component.properties.numItems; i++) {
    var prop = component.properties[i];
    var shown = null;
    try { shown = String(prop.displayName); } catch (eShown) {}
    if (shown !== "Uniform Scale" && shown !== "Escala uniforme") continue;
    try {
      var value = prop.getValue();
      return value === true || value === 1;
    } catch (eUniform) {
      return false;
    }
  }
  return false;
}

// True when the display name actual is the property wanted: the exact name, or "Scale" and "Scale Height" while
// Uniform Scale is on (__propertyNameMatches, bridge/script-builder.js, lines 694-699).
function __propertyNameMatches(actual, wanted, component) {
  actual = String(actual);
  wanted = String(wanted);
  if (actual === wanted) return true;
  var aliased = (wanted === "Scale" && actual === "Scale Height") || (wanted === "Scale Height" && actual === "Scale");
  return aliased && __isUniformScale(component);
}

// The display names of the component, for a refusal, joined by ", "; a name that two properties share gets
// " (property_index N)"; at most 25 (__availablePropertyNames, bridge/script-builder.js, lines 701-722).
function __availablePropertyNames(component) {
  var names = [];
  var counts = {};
  var total = 0;
  for (var i = 0; i < component.properties.numItems; i++) {
    var name = "";
    try { name = String(component.properties[i].displayName || ""); } catch (eName) {}
    if (!name.replace(/\s/g, "")) continue;
    total++;
    names.push({name: name, index: i});
    var key = "$" + name;
    counts[key] = (counts[key] || 0) + 1;
  }
  var shown = [];
  var limit = Math.min(names.length, 25);
  for (var n = 0; n < limit; n++) {
    var entry = names[n];
    shown.push(counts["$" + entry.name] > 1 ? entry.name + " (property_index " + entry.index + ")" : entry.name);
  }
  var summary = shown.length ? shown.join(", ") : "none with a non-empty display name";
  if (total > limit) summary += " (first " + limit + " of " + total + ")";
  return summary;
}

// The one property of component whose display name is name (no trimming), by the rule of __propertyNameMatches.
// Returns {property, index}. Two properties with the name are refused (Opacity has two named Blend Mode); pass the
// index to __property then.
function __propertyNamed(component, name) {
  var wanted = String(name);
  var effect = String(component.displayName);
  var found = null;
  var count = 0;
  for (var i = 0; i < component.properties.numItems; i++) {
    var shown = null;
    try { shown = String(component.properties[i].displayName); } catch (eName) {}
    if (shown !== null && __propertyNameMatches(shown, wanted, component)) {
      if (!found) found = {property: component.properties[i], index: i};
      count++;
    }
  }
  if (count === 0) {
    throw __precondition("no property named " + wanted + " in " + effect + ". Available properties: " +
                         __availablePropertyNames(component) + ".");
  }
  if (count > 1) {
    throw __precondition("ambiguous_property: " + count + " properties of " + effect + " are named " + wanted +
                         "; pass property_index");
  }
  return found;
}

// A key value as it goes out: a finite number, an array of finite numbers, or null.
function __keyValue(raw) {
  if (typeof raw === "number") return isFinite(raw) ? raw : null;
  if (raw !== null && typeof raw === "object" && Object.prototype.toString.call(raw) === "[object Array]") {
    var items = [];
    for (var n = 0; n < raw.length; n++) {
      if (typeof raw[n] !== "number" || !isFinite(raw[n])) return null;
      items.push(raw[n]);
    }
    return items;
  }
  return null;
}

// What one property holds: {is_time_varying, keys: [{ticks, value}], value}. A property with keys has value null
// and its keys in time order as getKeys gives them. A property with no keys has keys [] and its value.
function __readKeys(prop) {
  var varying = false;
  try {
    varying = !!prop.isTimeVarying();
  } catch (eVarying) {
    throw __precondition("Premiere would not say whether this property has keys");
  }
  if (!varying) return {is_time_varying: false, keys: [], value: __readValue(prop).value};
  if (__readColor(prop)) {
    throw __precondition("keyframed colour: Premiere gives the key times of a colour, but no colour value at a key");
  }
  var found = null;
  try {
    found = prop.getKeys();
  } catch (eKeys) {
    throw __precondition("the key list could not be read");
  }
  var count = 0;
  if (found !== 0 && found !== undefined) {
    if (found === null || typeof found !== "object" || typeof found.length !== "number" || !isFinite(found.length) ||
        found.length < 0 || Math.floor(found.length) !== found.length) {
      throw __precondition("the key list could not be read");
    }
    count = found.length;
  }
  var list = [];
  for (var k = 0; k < count; k++) {
    var key = found[k];
    var ticks = null;
    try { ticks = key ? __ticks(key) : null; } catch (eTime) {}
    if (ticks === null) throw __precondition("a key time could not be read");
    var value = null;
    try { value = __keyValue(prop.getValueAtKey(key)); } catch (eValue) {}
    list.push({ticks: ticks, value: value});
  }
  return {is_time_varying: true, keys: list, value: null};
}

// What a clip says about its time: {in_ticks, out_ticks, start_ticks, end_ticks (strings or null), speed (a
// finite number or null), reversed (true, false or null)}. Each reading sits in its own try: a clip that will not
// say gives null for that field and the rest still come.
function __clipFacts(clip) {
  var speed = null;
  try {
    speed = clip.getSpeed();
    if (typeof speed !== "number" || !isFinite(speed)) speed = null;
  } catch (eSpeed) {
    speed = null;
  }
  var reversed = null;
  try {
    var state = clip.isSpeedReversed();
    if (state === true || state === 1) reversed = true;
    else if (state === false || state === 0) reversed = false;
  } catch (eReversed) {
    reversed = null;
  }
  return {
    in_ticks: __timeTicks(clip, "inPoint"),
    out_ticks: __timeTicks(clip, "outPoint"),
    start_ticks: __timeTicks(clip, "start"),
    end_ticks: __timeTicks(clip, "end"),
    speed: speed,
    reversed: reversed
  };
}
