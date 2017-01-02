import asyncio
import itertools
import json
import string
import time
import traceback

import functools
import os
import random
import re

from spc import interface

_autocomplete_definitions = {}
_now_initializing = set()
_needs_initialization_next = set()
_rewake_condition = None
_keyword = ''


def _group_words_by(words, by):
    iterable = iter(words)
    return iter(lambda: list(itertools.islice(iterable, by)), [])


@asyncio.coroutine
def initialize_all(loop, connection):
    """
    :type loop: asyncio.events.AbstractEventLoop
    :type connection: spc.communication.ActiveConnection
    """
    global _autocomplete_definitions, _now_initializing, _needs_initialization_next, _keyword, _rewake_condition

    # Load cached data if available and recent enough
    try:
        loaded_definitions = json.load(open('.autocomplete_data.json'))
    except (FileNotFoundError, ValueError):
        pass
    else:
        last_updated = loaded_definitions['last_update']
        # Update every 5 days
        if last_updated - time.time() <= 60 * 60 * 24 * 5:
            del loaded_definitions['last_update']
            _autocomplete_definitions = loaded_definitions
            return

    interface.output_text("Creating autocompletion data.")

    _autocomplete_definitions = {}
    _now_initializing = set()
    _needs_initialization_next = {'global'}
    _keyword = '__ld_{}:'.format(''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase
                                                       + string.digits) for _ in range(5)))
    _rewake_condition = asyncio.Condition(loop=loop)
    while True:
        yield from _rewake_condition.acquire()
        try:
            all_needed_iterate = iter(set(x for x in itertools.chain(_now_initializing, _needs_initialization_next)
                                          if x not in _autocomplete_definitions))
            _now_initializing = set(itertools.islice(all_needed_iterate, 0, 100))
            _needs_initialization_next = set(itertools.islice(all_needed_iterate, 200, None))

            if _now_initializing:
                command_futures = []
                for chunk_of_words in _group_words_by(_now_initializing, 20):
                    command = (
                        '[{}].map(w=>"{}"+w+"="+JSON.stringify(_.get(global,w)?'
                        'Object.getOwnPropertyNames(_.get(global,w)):[])).join("\\n")'.format(
                            ','.join('"{}"'.format(word) for word in chunk_of_words),
                            _keyword
                        )
                    )
                    command_futures.append(connection.send_command(command))

                # If a minute has passed, just re-send any remaining requests
                # TODO: hacky way of setting a timeout, but it works! Plus, this way the timeout only starts once all
                # commands are sent, AND we start watching listening for definitions immediately!
                wake_up_event = asyncio.Event(loop=loop)

                @asyncio.coroutine
                def run_stuff_and_timeout(event):
                    yield from asyncio.gather(*command_futures, loop=loop)
                    yield from asyncio.sleep(60, loop=loop)
                    event.set()
                    yield from _rewake_condition.acquire()
                    try:
                        _rewake_condition.notify()
                    finally:
                        _rewake_condition.release()

                timeout_future = asyncio.ensure_future(run_stuff_and_timeout(wake_up_event), loop=loop)
                yield from _rewake_condition.wait_for(lambda: wake_up_event.is_set() or len(_now_initializing) < 20)
                if len(_now_initializing):
                    yield from asyncio.sleep(5, loop=loop)
                timeout_future.cancel()
            else:
                _keyword = ''
                _now_initializing = None
                _needs_initialization_next = None
                _rewake_condition = None
                break
        finally:
            _rewake_condition.release()

    # See http://stackoverflow.com/q/41421487/1907543 (PyCharm incorrectly detects this as unreachable due to a while
    #  loop with the break condition with a try/finally block)
    # noinspection PyUnreachableCode
    interface.output_text("Finished loading autocompletion data - saving to .autocomplete_data.json.")
    to_save = dict(**_autocomplete_definitions)
    to_save['last_update'] = round(time.time())
    try:
        json.dump(to_save, open('.autocomplete_data.json~', mode='w'))
        os.rename('.autocomplete_data.json~', '.autocomplete_data.json')
    except EnvironmentError:
        interface.output_text("Failed to save autocompletion data!")
        interface.output_text(''.join(traceback.format_exc()))


def is_definition(message):
    return bool(_keyword) and message.lstrip().startswith(_keyword)


@asyncio.coroutine
def load_definition(loop, text):
    """
    :type loop: asyncio.events.AbstractEventLoop
    :type text: str
    """
    if not _keyword:
        return
    text = text.strip()
    if not text.startswith(_keyword):
        raise ValueError("Invalid text to load")
    if '\n' in text:
        for part in text.split('\n'):
            asyncio.ensure_future(load_definition(loop, part))
        return

    name, value = text[len(_keyword):].split('=', 1)

    # TODO: this is completely trusting the server to only send what we expect.
    try:
        completions = json.loads(value)
    except ValueError as e:
        interface.output_text('Failed to decode autocomplete data response! (data: `{}`, error: `{}`)'.format(value, e))
        return

    yield from _rewake_condition.acquire()
    try:
        _autocomplete_definitions[name] = completions
        # TODO: add support for more 'deep' autocomplete through prototype detection
        # As it stands, we technically _could_ allow for more deep autocomplete with the current setup, but it would
        # lead to much more data being stored than would be needed, and a lot of unnecessary (and long) command data
        # transfers.
        if '.' not in name:
            if name == 'global':
                _needs_initialization_next.update(item for item in completions)
            else:
                _needs_initialization_next.update('{}.{}'.format(name, item) for item in completions)
        if name in _now_initializing:
            _now_initializing.remove(name)
        _rewake_condition.notify(1)
    finally:
        _rewake_condition.release()


@functools.lru_cache()
def completions_for(text):
    """
    :param text: The text to complete for.
    :type text: str
    :return: A list of possible completions
    :rtype: list[str]
    """
    if not _autocomplete_definitions:
        return []
    if '.' in text:
        parent, text = text.rsplit('.', 1)
        prefix = parent + '.'
    else:
        parent = 'global'
        prefix = ''

    if parent in _autocomplete_definitions:
        text_match = re.compile(re.escape(text), re.I)
        return [prefix + word for word in _autocomplete_definitions[parent] if text_match.match(word)]
    else:
        return []
