import asyncio
import json

from spc import interface, communication, autocompletion

interface.initialize_readline()

loop = asyncio.get_event_loop()

config = json.load(open('console.json'))

connection = communication.ActiveConnection(loop, config['user'], config['password'],
                                            config.get('ws_url'), config.get('api_url'))

input_loop_future = None
try:
    # Start the connection, and initialize autocompletion once we have connected.
    asyncio.ensure_future(connection.connect(), loop=loop).add_done_callback(
        lambda _: asyncio.ensure_future(autocompletion.initialize_all(loop, connection), loop=loop))
    input_loop_future = asyncio.ensure_future(interface.input_loop(asyncio.get_event_loop(), connection), loop=loop)
    loop.run_until_complete(input_loop_future)
except (EOFError, KeyboardInterrupt):
    if input_loop_future and not input_loop_future.cancelled():
        input_loop_future.cancel()
    loop.run_until_complete(connection.close())
