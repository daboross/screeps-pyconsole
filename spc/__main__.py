import asyncio
import json

from spc import interface, communication

interface.initialize_readline()

loop = asyncio.get_event_loop()

config = json.load(open('console.json'))

connection = communication.ActiveConnection(loop, config['user'], config['password'],
                                            config.get('ws_url'), config.get('api_url'))

try:
    loop.run_until_complete(asyncio.gather(
        connection.connect(),
        interface.input_loop(asyncio.get_event_loop(), connection),
        loop=loop
    ))
except (EOFError, KeyboardInterrupt):
    loop.run_until_complete(connection.close())
