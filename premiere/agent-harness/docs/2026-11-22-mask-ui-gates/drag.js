ObjC.import('CoreGraphics');
function run(argv) {
  var x0 = parseFloat(argv[0]), y0 = parseFloat(argv[1]), x1 = parseFloat(argv[2]), y1 = parseFloat(argv[3]), tap = $.kCGHIDEventTap, n = 14;
  $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, $.CGPointMake(x0 - 3, y0 - 3), 0)); delay(0.3);
  $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, $.CGPointMake(x0, y0), 0)); delay(0.4);
  $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDown, $.CGPointMake(x0, y0), 0)); delay(0.15);
  for (var i = 1; i <= n; i++) { var p = $.CGPointMake(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n);
    $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDragged, p, 0)); delay(0.04); }
  delay(0.2); $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseUp, $.CGPointMake(x1, y1), 0));
  return 'drag ' + [x0, y0, x1, y1].join(',');
}
