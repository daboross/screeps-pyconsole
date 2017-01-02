import asyncio
import json

import functools
import requests
import websockets

from spc import interface, autocompletion

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

    @asyncio.coroutine
    def connect(self):
        retry = 3
        while retry > 0 and not self._done:
            try:
                self._connection = yield from websockets.connect(self._ws_url, loop=self._loop)
            except websockets.exceptions.InvalidHandshake:
                pass
            else:
                if self._connection is not None:
                    break

            interface.output_text("Failed to connect, retrying.", False)
            retry -= 1
            yield from asyncio.sleep(3, loop=self._loop)

        if self._done:
            if self._connection is not None:
                self._connection.close()
            return

        if self._token is None:
            yield from self._login()

        yield from self._connection.send('auth {}'.format(self._token))
        asyncio.ensure_future(self.recv_loop(), loop=self._loop)

    @asyncio.coroutine
    def recv_loop(self):
        while True:
            try:
                message = yield from self._connection.recv()
            except (websockets.exceptions.InvalidState, ConnectionError):
                if self._done:
                    interface.output_text("Connection closed.", False)
                else:
                    interface.output_text("Reconnecting.", False)

                    @asyncio.coroutine
                    def reconnect():
                        yield from self._connection.close()
                        self._connection = None
                        yield from asyncio.sleep(3, loop=self._loop)
                        yield from self.connect()

                    asyncio.ensure_future(reconnect(), loop=self._loop)
                break
            if message.startswith('auth ok '):
                yield from self._connection.send('subscribe user:{}/console'.format(self._user_id))
                interface.output_text("Connected.", False)
                self._ready = True
                asyncio.ensure_future(self._send_queued_commands(), loop=self._loop)
                continue
            else:
                try:
                    message_json = json.loads(message)
                except ValueError:
                    if not isinstance(message, str) or (not message.startswith('time ')
                                                        and not message.startswith('protocol ')
                                                        and not message.startswith('package ')):
                        interface.output_text("Unknown message: {}".format(message))
                    continue
                else:
                    if len(message_json) != 2:
                        interface.output_text("Unknown message: {}".format(message))
                    else:
                        # TODO: abstract out this monstrosity
                        for general_type, stuff in message_json[1].items():
                            if isinstance(stuff, str):
                                if stuff.startswith('['):
                                    interface.output_text("[{}]{}".format(general_type, stuff))
                                else:
                                    interface.output_text("[{}] {}".format(general_type, stuff))
                            elif isinstance(stuff, dict):
                                for specific_type, text_list in stuff.items():
                                    if len(text_list):
                                        if isinstance(text_list, list):
                                            for text in text_list:
                                                if general_type == 'messages' \
                                                        and specific_type == 'results' \
                                                        and autocompletion.is_definition(text):
                                                    asyncio.ensure_future(
                                                        autocompletion.load_definition(self._loop, text))
                                                elif text.startswith('['):
                                                    interface.output_text("[{}][{}]{}".format(
                                                        general_type, specific_type, text))
                                                else:
                                                    interface.output_text("[{}][{}] {}".format(
                                                        general_type, specific_type, text))
                                        else:
                                            if isinstance(text_list, str) and general_type == 'messages' \
                                                    and specific_type == 'results' \
                                                    and autocompletion.is_definition(text_list):
                                                asyncio.ensure_future(
                                                    autocompletion.load_definition(self._loop, text_list))
                                            elif isinstance(text_list, str) and text_list.startswith('['):
                                                interface.output_text("[{}][{}]{}".format(
                                                    general_type, specific_type, text_list))
                                            else:
                                                interface.output_text("[{}][{}] {}".format(
                                                    general_type, specific_type, text_list))
                            else:
                                interface.output_text("[{}][???] {}".format(general_type, stuff))

    @asyncio.coroutine
    def _send_queued_commands(self):
        if self._queued_commands is not None:
            futures = []
            for text in self._queued_commands:
                futures.append(self._send_command_call(text))
            self._queued_commands = None
            yield from asyncio.gather(*futures, loop=self._loop)

    @asyncio.coroutine
    def _login(self):
        login_result = yield from self._loop.run_in_executor(None, functools.partial(
            requests.post,
            self._api_url + '/auth/signin',
            json={'email': self._username, 'password': self._password}
        ))
        login_result.raise_for_status()
        login_json = login_result.json()
        if not login_json.get('ok') or not login_json.get('token'):
            raise ValueError("Non-OK result from logging in: {}".format(login_json))
        self._token = login_result.json()['token']
        info_result = yield from self._loop.run_in_executor(None, functools.partial(
            requests.get,
            self._api_url + '/auth/me',
            headers={'X-Username': self._token, 'X-Token': self._token}
        ))
        info_result.raise_for_status()
        info_json = info_result.json()
        if not info_json.get('ok') or not info_json.get('_id'):
            raise ValueError("Non-OK result from getting user info: {}".format(info_json))
        self._user_id = info_json['_id']
        if 'X-Token' in info_result.headers:
            self._token = info_result.headers['X-Token']

    @asyncio.coroutine
    def send_command(self, text):
        if not self._ready:
            if self._queued_commands:
                self._queued_commands.append(text)
            else:
                self._queued_commands = [text]
        else:
            if text.startswith('.'):
                self._connection.send(text[1:])
            else:
                yield from self._send_command_call(text)

    @asyncio.coroutine
    def _send_command_call(self, text, retry=3):
        result = yield from self._loop.run_in_executor(None, functools.partial(
            requests.post,
            self._api_url + '/user/console',
            headers={'X-Username': self._token, 'X-Token': self._token},
            json={'expression': text}
        ))
        assert isinstance(result, requests.Response)
        if not result.ok:
            result_json = result.json()
            if result_json and result_json.get('error') == 'unauthorized' and retry > 0:
                yield from self._login()
                return (yield from self._send_command_call(text, retry=retry - 1))
            interface.output_text("failed to send command: {} {}:\n{}".format(result.status_code,
                                                                              result.reason, result.text))
            return
        result_json = result.json()
        if 'X-Token' in result.headers:
            self._token = result.headers['X-Token']
        if not result_json.get('ok'):
            if result_json.get('error') == 'unauthorized' and retry > 0:
                yield from self._login()
                yield from self._send_command_call(text, retry=retry - 1)
            interface.output_text("failed to send command: non-OK result:\n{}".format(result_json))

    @asyncio.coroutine
    def close(self):
        self._done = True
        if self._connection:
            yield from self._connection.close()
            self._connection = None
