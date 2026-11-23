ObjC.import('CoreGraphics');
function run(argv) {
  var x = parseFloat(argv[1]), y = parseFloat(argv[2]), p = $.CGPointMake(x, y);
  var mv = $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, p, 0); $.CGEventPost($.kCGHIDEventTap, mv);
  if (argv[0] == 'click') { delay(1.2);
    var d = $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDown, p, 0); $.CGEventPost($.kCGHIDEventTap, d); delay(0.08);
    var u = $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseUp, p, 0); $.CGEventPost($.kCGHIDEventTap, u); }
  return argv[0] + ' at ' + x + ',' + y;
}
