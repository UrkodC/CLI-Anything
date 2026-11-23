ObjC.import('CoreGraphics');
function run(argv) {
  var x = parseFloat(argv[0]), y = parseFloat(argv[1]), dy = parseInt(argv[2]), p = $.CGPointMake(x, y);
  $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, p, 0)); delay(0.3);
  var e = $.CGEventCreateScrollWheelEvent2(null, 0, 1, dy, 0, 0); $.CGEventPost($.kCGHIDEventTap, e); return 'scrolled ' + dy;
}
