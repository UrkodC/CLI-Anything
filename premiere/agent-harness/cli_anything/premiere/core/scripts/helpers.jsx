// Helpers carried by every script the CLI sends to Premiere. This text goes inside one wrapper
// function, so nothing here reaches the global scope. ES3 only: Premiere's ExtendScript has no
// built-in JSON object, so the reply is written by hand.

function __hex4(code) {
  var h = code.toString(16);
  while (h.length < 4) h = "0" + h;
  return "\\u" + h;
}

// A string as a quoted JSON string. Control characters below 0x20 and every character above 0x7E
// become \uXXXX, so the reply is plain ASCII whatever the project holds.
function __quote(text) {
  var out = '"';
  for (var i = 0; i < text.length; i++) {
    var ch = text.charAt(i);
    var code = text.charCodeAt(i);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (code < 0x20 || code > 0x7E) out += __hex4(code);
    else out += ch;
  }
  return out + '"';
}

// Strings, finite numbers, true/false, null, arrays and plain objects (own properties only).
// undefined, NaN, infinities and functions become null.
function __json(value) {
  if (value === null || value === undefined) return "null";
  var kind = typeof value;
  if (kind === "string") return __quote(value);
  if (kind === "number") return isFinite(value) ? String(value) : "null";
  if (kind === "boolean") return value ? "true" : "false";
  if (kind === "function") return "null";
  var parts = [];
  if (Object.prototype.toString.call(value) === "[object Array]") {
    for (var i = 0; i < value.length; i++) parts.push(__json(value[i]));
    return "[" + parts.join(",") + "]";
  }
  for (var key in value) {
    if (Object.prototype.hasOwnProperty.call(value, key)) parts.push(__quote(String(key)) + ":" + __json(value[key]));
  }
  return "{" + parts.join(",") + "}";
}

function __ok(data) {
  return __json({success: true, data: data});
}

function __fail(message) {
  return __json({success: false, error: String(message)});
}
