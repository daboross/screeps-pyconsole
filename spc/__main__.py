import asyncio
import json

import os

from spc import interface, communication, autocompletion

interface.initialize_readline(autocompletion.completions_for)

loop = asyncio.get_event_loop()

config = json.load(open('console.json'))

connection = communication.ActiveConnection(loop, config['user'], config['password'],
                                            config.get('ws_url'), config.get('api_url'))


@asyncio.coroutine
def start():
    yield from connection.connect()
    interface.initialize_signal_handlers(loop)
    yield from asyncio.gather(
        interface.input_loop(loop, connection),
        autocompletion.initialize_all(loop, connection),
        loop=loop
    )


main_task = asyncio.ensure_future(start())
loop.run_until_complete(main_task)
loop.run_until_complete(connection.close())
loop.close()
os._exit(0)
