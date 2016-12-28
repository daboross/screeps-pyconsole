import asyncio
import readline
from time import strftime

from spc import autocompletion


def output_text(text):
    print('\r  {}'.format(' ' * len(readline.get_line_buffer())), end='\r', flush=True)
    if text.startswith('['):
        print(strftime('[%m-%d %H:%M %S]'), text.strip(), sep='')
    else:
        print(strftime('[%m-%d %H:%M %S]'), text.strip())
    print('> {}'.format(readline.get_line_buffer()), end='', flush=True)


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
    while True:
        result = await loop.run_in_executor(None, input, '> ')
        asyncio.ensure_future(connection.send_command(result.strip()), loop=loop)
