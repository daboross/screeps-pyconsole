import asyncio
import json

import colorama
import functools
import re
import requests
import websockets

from spc import autocompletion, interface

DEFAULT_WS_URL = 'wss://screeps.com/socket/websocket'
DEFAULT_API_URL = 'https://screeps.com/api'

html_script_regex = re.compile('\s*<script>.*</script>\s*', re.IGNORECASE)
error_regex = re.compile('error', re.IGNORECASE)

def function():
    pass

def process_received_message(message, source='log'):
    if source == 'log':
        if html_script_regex.match(message):
            return
        if error_regex.search(message):
            interface.output_text(message, color=colorama.Fore.RED)
        else:
            interface.output_text(message)
    elif source == 'results':
        if html_script_regex.match(message):
            return
        interface.output_text(message, date=False)
    elif source == 'error':
        interface.output_text('[error!] ' + message, color=colorama.Fore.RED)
    else:
        interface.output_text("[unknown type! {}]".format(source, message), color=colorama.Fore.RED)


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
                yield from self.close(True)
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
                        yield from self.close(True)
                        yield from asyncio.sleep(3, loop=self._loop)
                        yield from self.connect()

                    asyncio.ensure_future(reconnect(), loop=self._loop)
                break
            if message.startswith('auth ok'):
                yield from self._connection.send('subscribe user:{}/console'.format(self._user_id))
                interface.output_text("Connected.", False)
                self._ready = True
                asyncio.ensure_future(self._send_queued_commands(), loop=self._loop)
                continue
            elif message.startswith('auth failed'):
                self._ready = False
                yield from self._connection.close()
                yield from self._login()
                yield from self.connect()
                return
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
                                process_received_message(stuff, general_type)
                            elif isinstance(stuff, dict):
                                for specific_type, text_list in stuff.items():
                                    if len(text_list):
                                        if isinstance(text_list, list):
                                            for text in text_list:
                                                if general_type == 'messages' \
                                                        and specific_type == 'results':
                                                    if autocompletion.is_definition(text):
                                                        asyncio.ensure_future(
                                                            autocompletion.load_definition(self._loop, text))
                                                    else:
                                                        process_received_message(text, 'results')
                                                elif general_type == 'messages' and specific_type == 'log':
                                                    process_received_message(text)
                                                else:
                                                    process_received_message(text, specific_type)
                                        else:
                                            if isinstance(text_list, str) and general_type == 'messages' \
                                                    and specific_type == 'results':
                                                if autocompletion.is_definition(text_list):
                                                    asyncio.ensure_future(
                                                        autocompletion.load_definition(self._loop, text_list))
                                                else:
                                                    process_received_message(text_list, 'results')
                                            elif general_type == 'messages' and specific_type == 'log':
                                                process_received_message(str(text_list))
                                            else:
                                                process_received_message(str(text_list), specific_type)
                            else:
                                process_received_message(str(stuff), general_type)

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
        if self._done:
            return
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
        try:
            result = yield from self._loop.run_in_executor(None, functools.partial(
                requests.post,
                self._api_url + '/user/console',
                headers={'X-Username': self._token, 'X-Token': self._token},
                json={'expression': text}
            ))
        except ConnectionError as e:
            interface.output_text("Failed to send command: {}".format(
                e), False)
            return
        if not result.ok:
            result_json = result.json()
            if result_json and result_json.get('error') == 'unauthorized' and retry > 0:
                yield from self._login()
                return (yield from self._send_command_call(text, retry=retry - 1))
            interface.output_text("Failed to send command: HTTP Error {}: {}:\n{}".format(
                result.status_code, result.reason, result.text), False)
            return
        result_json = result.json()
        if 'X-Token' in result.headers:
            self._token = result.headers['X-Token']
        if not result_json.get('ok'):
            if result_json.get('error') == 'unauthorized' and retry > 0:
                yield from self._login()
                yield from self._send_command_call(text, retry=retry - 1)
            interface.output_text("Failed to send command: non-OK result:\n{}".format(
                result_json), False)

    @asyncio.coroutine
    def close(self, reconnecting_already=False):
        if not reconnecting_already:
            self._done = True
        if self._connection:
            try:
                yield from self._connection.close()
            except ConnectionError:
                pass
            self._connection = None
