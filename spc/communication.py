import asyncio
import json

import functools
import requests
import websockets

from spc import interface

DEFAULT_WS_URL = 'wss://screeps.com/socket/websocket'
DEFAULT_API_URL = 'https://screeps.com/api'


class ActiveConnection:
    """
    :type _loop: asyncio.events.AbstractEventLoop
    :type _username: str
    :type _password: str
    :type _ws_url: str
    :type _connection: websockets.WebSocketClientProtocol
    :type _queued_commands: list[str]
    """

    def __init__(self, loop, username, password, ws_url=None, api_url=None):
        self._loop = loop
        self._username = username
        self._password = password
        self._ws_url = ws_url or DEFAULT_WS_URL
        self._api_url = api_url or DEFAULT_API_URL
        self._connection = None
        self._user_id = None
        self._token = None
        self._queued_commands = None
        self._ready = False
        self._done = False
        pass

    async def connect(self):
        self._connection = await websockets.connect(self._ws_url, loop=self._loop)
        if self._connection is None:
            raise ValueError("Failed to initiate websockets connection.")

        if self._token is None:
            await self._login()

        await self._connection.send('auth {}'.format(self._token))
        asyncio.ensure_future(self.recv_loop(), loop=self._loop)

    async def recv_loop(self):
        while True:
            try:
                message = await self._connection.recv()
            except websockets.exceptions.ConnectionClosed:
                if self._done:
                    interface.output_text("Connection closed.")
                else:
                    interface.output_text("Reconnecting.")
                    asyncio.ensure_future(self.connect)
                break
            if message.startswith('auth ok '):
                await self._connection.send('subscribe user:{}/console'.format(self._user_id))
                interface.output_text("Subscribed successfully.")
                self._ready = True
                asyncio.ensure_future(self._send_queued_commands(), loop=self._loop)
                continue
            else:
                try:
                    message_json = json.loads(message)
                except ValueError:
                    interface.output_text("Unknown message: {}".format(message))
                    continue
                else:
                    if len(message_json) != 2:
                        interface.output_text("Unknown message: {}".format(message))
                    else:
                        # TODO: abstract out this monstrosity
                        for general_type, stuff in message_json[1].items():
                            if isinstance(stuff, str):
                                if len(stuff):
                                    if stuff.startswith('['):
                                        interface.output_text("[{}]{}".format(general_type, stuff))
                                    else:
                                        interface.output_text("[{}] {}".format(general_type, stuff))
                            elif isinstance(stuff, dict):
                                for specific_type, text_list in stuff.items():
                                    if len(text_list):
                                        if isinstance(text_list, list):
                                            for text in text_list:
                                                if text.startswith('['):
                                                    interface.output_text("[{}][{}]{}".format(
                                                        general_type, specific_type, text))
                                                else:
                                                    interface.output_text("[{}][{}] {}".format(
                                                        general_type, specific_type, text))
                                        else:
                                            if isinstance(text_list, str) and text_list.startswith('['):
                                                interface.output_text("[{}][{}]{}".format(
                                                    general_type, specific_type, text_list))
                                            else:
                                                interface.output_text("[{}][{}] {}".format(
                                                    general_type, specific_type, text_list))
                            else:
                                interface.output_text("[{}][???] {}".format(general_type, stuff))

    async def _send_queued_commands(self):
        if self._queued_commands is not None:
            for text in self._queued_commands:
                await self._send_command_call(text)
            self._queued_commands = None

    async def _login(self):
        login_result = await self._loop.run_in_executor(None, functools.partial(
            requests.post,
            self._api_url + '/auth/signin',
            json={'email': self._username, 'password': self._password}
        ))
        login_result.raise_for_status()
        login_json = login_result.json()
        if not login_json.get('ok') or not login_json.get('token'):
            raise ValueError("Non-OK result from logging in: {}".format(login_json))
        self._token = login_result.json()['token']
        info_result = await self._loop.run_in_executor(None, functools.partial(
            requests.get,
            self._api_url + '/auth/me',
            headers={'X-Username': self._token, 'X-Token': self._token}
        ))
        info_result.raise_for_status()
        info_json = info_result.json()
        if not info_json.get('ok') or not info_json.get('_id'):
            raise ValueError("Non-OK result from getting user info: {}".format(info_json))
        self._user_id = info_json['_id']
        interface.output_text("Found user ID: {}".format(self._user_id))
        if 'token' in info_json:
            self._token = info_json['token']

    async def send_command(self, text):
        if not self._ready:
            if self._queued_commands:
                self._queued_commands.append(text)
            else:
                self._queued_commands = [text]
        else:
            if text.startswith('.'):
                self._connection.send(text[1:])
            else:
                await self._send_command_call(text)

    async def _send_command_call(self, text, retry=3):
        result = await self._loop.run_in_executor(None, functools.partial(
            requests.post,
            self._api_url + '/user/console',
            headers={'X-Username': self._token, 'X-Token': self._token},
            json={'expression': text}
        ))
        assert isinstance(result, requests.Response)
        if not result.ok:
            result_json = result.json()
            if result_json and result_json.get('error') == 'unauthorized' and retry > 0:
                await self._login()
                await self._send_command_call(text, retry=retry - 1)
            interface.output_text("failed to send command: {} {}:\n{}".format(result.status_code,
                                                                              result.reason, result.text))
            return
        result_json = result.json()
        if 'token' in result_json:
            self._token = result_json['token']
        if not result_json.get('ok'):
            if result_json.get('error') == 'unauthorized' and retry > 0:
                await self._login()
                await self._send_command_call(text, retry=retry - 1)
            interface.output_text("failed to send command: non-OK result:\n{}".format(result_json))

    async def close(self):
        self._done = True
        await self._connection.close()
