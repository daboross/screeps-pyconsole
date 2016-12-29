import asyncio
import readline
from time import strftime

from spc import autocompletion

_input_loop_running = None


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


def _completion(word, state):
    matches = autocompletion.completions_for(word)
    if state < len(matches):
        return matches[state]


def initialize_readline():
    readline.parse_and_bind("tab: menu-complete")
    readline.parse_and_bind("\C-space: menu-complete")

    readline.set_completer(_completion)


async def input_loop(loop, connection):
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
            result = await loop.run_in_executor(None, input, '> ')
            asyncio.ensure_future(connection.send_command(result.strip()), loop=loop)
    finally:
        _input_loop_running.clear()
