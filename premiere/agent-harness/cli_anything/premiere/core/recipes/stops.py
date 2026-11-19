"""One test for "the user stopped the run", and one rule for "the write was not sent", shared by the recipes.

A stop (Ctrl-C, SIGTERM, SIGHUP) reaches a recipe in three shapes:

- KeyboardInterrupt: Ctrl-C outside a signal section of the bridge, or without the CLI's signal handlers;
- bridge.Interrupted: SIGTERM or SIGHUP outside a signal section (with the CLI's handlers, which also turn
  Ctrl-C inside a section into the third shape);
- CliError with the code "interrupted": the bridge held the signal back inside BridgeClient.send and reports the
  stop itself, and McpSession does the same for a request that was waiting.

For a WRITE, BridgeClient.send raises "interrupted" only when Premiere had not picked the command up (the command
file was never made, or this client could still delete it): nothing was changed. A stop after the pickup of a
write comes out as "unknown_outcome", with a recovery marker. That is not a stop for is_stop: its message is the
only thing that tells it from a timeout, and a message is never parsed. ends_writes covers it: after it, as after
a stop, no further write is sent.
"""
from __future__ import annotations

from cli_anything.premiere.core import bridge
from cli_anything.premiere.errors import CliError

STOPPED = "Stopped by Ctrl-C or a stop signal."

# The one "nothing was sent" rule. A write that fails with one of these codes never reached Premiere:
# - precondition: the write script refused before its first host write (find.jsx, __expect, its own checks);
# - recovery_required: WriteSession.write found a recovery marker before the send;
# - bridge_down, script_too_large: BridgeClient.send refused before it made the command file;
# - bad_args: the session refused the script arguments before the send;
# - not_applied: BridgeClient.send gave up waiting, and then deleted its own command file (or never made it), so
#   Premiere never picked the command up. A dialog that blocks the panel ends a write this way.
# "interrupted" is not in this tuple, though the bridge raises it for a write only before the pickup (nothing of
# that send was changed). A stop is never a refusal to go on from: code that reads this tuple to decide "refused,
# go on" (lut.py, index 6) or "no record of the entry" (effects.write_entry) must still see the stop. Use
# not_sent() where only the question "did this send reach Premiere?" matters.
NOT_SENT_CODES = ("precondition", "recovery_required", "bridge_down", "script_too_large", "bad_args", "not_applied")


def is_stop(exc: BaseException | None) -> bool:
    """True when `exc` is a stop by the user, in any of its three shapes."""
    if isinstance(exc, (KeyboardInterrupt, bridge.Interrupted)):
        return True
    return isinstance(exc, CliError) and exc.code == "interrupted"


def ends_writes(exc: BaseException | None) -> bool:
    """True when no further write may be sent in this run after `exc`: a stop, or a write whose outcome is
    unknown."""
    return is_stop(exc) or (isinstance(exc, CliError) and exc.code == "unknown_outcome")


def not_sent(exc: BaseException | None) -> bool:
    """True when the failed write `exc` was never picked up by Premiere: a CliError with a code of NOT_SENT_CODES,
    or "interrupted" (BridgeClient.send raises it for a write only before the pickup). A stop in another shape
    (KeyboardInterrupt, bridge.Interrupted) does not say whether the write was sent: False."""
    return isinstance(exc, CliError) and (exc.code in NOT_SENT_CODES or exc.code == "interrupted")


def as_error(exc: BaseException, message: str = STOPPED) -> CliError:
    """The stop `exc` as a CliError: itself when it is one already, else CliError("interrupted", message)."""
    return exc if isinstance(exc, CliError) else CliError("interrupted", message)
