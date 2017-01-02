import asyncio
import json

from spc import interface, communication, autocompletion

interface.initialize_readline(autocompletion.completions_for)

loop = asyncio.get_event_loop()

config = json.load(open('console.json'))

connection = communication.ActiveConnection(loop, config['user'], config['password'],
                                            config.get('ws_url'), config.get('api_url'))

input_loop_future = None


@asyncio.coroutine
def start():
    global loop, config, connection, input_loop_future

    yield from connection.connect()
    asyncio.ensure_future(autocompletion.initialize_all(loop, connection))
    input_loop_future = asyncio.ensure_future(interface.input_loop(asyncio.get_event_loop(), connection), loop=loop)
    yield from input_loop_future


try:
    loop.run_until_complete(start())
except (EOFError, KeyboardInterrupt):
    if input_loop_future and not input_loop_future.cancelled():
        input_loop_future.cancel()
    loop.run_until_complete(connection.close())
