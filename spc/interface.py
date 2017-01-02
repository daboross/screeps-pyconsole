import asyncio
import readline
from asyncio.tasks import FIRST_COMPLETED
from time import strftime

import signal

_input_loop_running = None
_exit_required = None


def output_text(text, date=True):
    print('\r  {}'.format(' ' * len(readline.get_line_buffer())), end='\r', flush=True)
    if date:
        print(strftime('[%m-%d %H:%M]'), text.strip())
    else:
        print(text.strip())
    if _input_loop_running is not None and _input_loop_running.is_set():
        print('> {}'.format(readline.get_line_buffer()), end='', flush=True)
    else:
        print('{}'.format(readline.get_line_buffer()), end='', flush=True)


def _completion(completer):
    def complete(word, state):
        matches = completer(word)
        if state < len(matches):
            return matches[state]


def initialize_readline(completer):
    readline.parse_and_bind("tab: menu-complete")
    readline.parse_and_bind("\C-space: menu-complete")

    readline.set_completer(_completion(completer))


def initialize_signal_handlers(loop):
    """
    :type loop: asyncio.events.AbstractEventLoop
    """
    global _exit_required
    _exit_required = asyncio.Event(loop=loop)

    def handler():
        loop.call_soon_threadsafe(_exit_required.set)
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)

    loop.add_signal_handler(signal.SIGINT, handler)
    loop.add_signal_handler(signal.SIGTERM, handler)


@asyncio.coroutine
def input_loop(loop, connection):
    """
    :type loop: asyncio.events.AbstractEventLoop
    :type connection: spc.communication.ActiveConnection
    """
    global _input_loop_running
    if _input_loop_running is None:
        _input_loop_running = asyncio.Event(loop=loop)
    _input_loop_running.set()
    try:
        while True:
            done, pending = yield from asyncio.wait([
                loop.run_in_executor(None, input, '> '),
                _exit_required.wait()
            ], loop=loop, return_when=FIRST_COMPLETED)
            for x in pending:
                x.cancel()
            done = [x.result() for x in done]
            if True in done:
                # only _exit_required.wait() returns something that equals True.
                # input will only ever return strings.
                return
            else:
                result = done[0]
            asyncio.ensure_future(connection.send_command(result.strip()), loop=loop)
    except (EOFError, KeyboardInterrupt):
        return
    finally:
        _input_loop_running.clear()
