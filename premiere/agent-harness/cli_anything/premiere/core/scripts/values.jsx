// values.jsx: how a recipe script reads the value of one effect property
// Function definitions only: nothing here runs when the script loads. ES3 only. Used by effect_get.jsx and
// effect_resolve.jsx, so that a read and a write see the same kinds.
// __readValue(prop) gives {kind, value}. kind is "number", "string", "boolean", "color" (value is
// [alpha, red, green, blue]), "array" (value is an array of finite numbers, such as Position) or "unreadable"
// (value is null: a read that gave nothing or threw, NaN, an infinity, a number of 4294967296 or more that is not
// a colour, or anything else). It never throws.
// Host calls follow the premiere-pro-mcp server (dist/), in the order of get_effect_properties
// (tools/keyframes.js, lines 100-110): first the colour, as __readColorValue does (bridge/script-builder.js,
// lines 662-674): prop.getColorValue() with four finite channels gives [alpha, red, green, blue]; it throws on a
// parameter that is not a colour. Then prop.getValue(0, 0), as __readableParamValue gets it
// (bridge/script-builder.js, lines 2049-2059); that function asks getColorValue a second time for a number of
// 4294967296 or more, which is the same question as the first one, so it is not asked again here. Such a number
// is a colour packed into one 64-bit integer, which a double cannot hold exactly (bridge/script-builder.js,
// lines 2045-2048). With no colour read behind it the number is lossy and must not be written back, so the value
// is "unreadable" and null.

// The colour read of __readColorValue: [alpha, red, green, blue], or null for anything that is not a colour.
function __readColor(prop) {
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
}

// {kind, value} for one property. The colour first, then getValue(0, 0), then the type of what it gave.
function __readValue(prop) {
  try {
    var color = __readColor(prop);
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
}
