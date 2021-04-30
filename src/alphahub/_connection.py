__all__ = ['Connection']

import asyncio
import inspect
import json
import logging
from asyncio import wait_for
from typing import Optional, Iterable, Callable

import requests
from datetime import datetime, timedelta

import websockets

from ._fsm import SimpleFSM, ExceptionState

OAUTH_TIMEOUT = timedelta(minutes=29) # it's actually 30 minutes but we leave a small buffer
HEARTBEAT_INTERVAL = timedelta(seconds=15)
SUBSCRIPTION_TIMEOUT = timedelta(seconds=15) # if subscriptions aren't confirmed within this time, it's an error
RECEIVE_TIMEOUT_SECONDS = 5


class Connection (SimpleFSM):
    def __init__(self, email:str, password:str, algo_ids:Iterable[int],
                 on_signal:Optional[Callable[[id,dict],None]]=None, log:logging.Logger=None):
        """
        :param email: your alphahub.us login email
        :param password: your alphahub.us login password
        :param algo_ids: a list of integer signal ID's to subscribe to. see README.md for values
        :param on_signal: a Function which is invoked whenever a new signal message is received from alphahub. The first
        argument is the algorithm ID, and the second argument is a dictionary of signal information that looks like
        this:

           {
              "close":[
                 {"price":216.08,"side":"buy","symbol":"VRTX","timestamp":"2021-04-26 T14:30:00"},
                 {"price":87.1,"side":"sell","symbol":"MU","timestamp":"2021-04-26T14:30:00"},
                 {"price":80.28,"side":"sell"," symbol":"BMRN","timestamp":"2021-04-26T14:30:00"}
              ],
              "open":[
                 {"price":168.56,"side":"buy","symbol":"JBHT","timestamp":"2021-04-26T14:30:01"},
                 {"price":414.6,"side":"buy","symbol":"ILMN","timestamp":"2021-04-26T14:30:01"},
                 {"price":142.94,"side":"sell","symbol":"CDNS","timestamp":"2021-04-26T14:30:01"}
              ]
           }

        If on_signal is not provided, then this class's on_signal() method is invoked instead. You can either pass in
        a callback function or override this class's on_signal() method.

        :param log: an optional logging.Logger used by this class. If not given, then logs are published to
            alphahub.Connection
        """
        super().__init__(log=log)
        self.email = email
        if not self.email:
            raise ValueError('empty email')
        self.password = password
        if not self.password:
            raise ValueError('empty password')
        self.algo_ids = list(algo_ids)
        if not self.algo_ids:
            raise ValueError('no algo_ids specified')
        self.token:Optional[str] = None
        self.renew_token:Optional[str] = None
        self.expiry:datetime = datetime.min
        self.ws:Optional[websockets.protocol.WebSocketCommonProtocol] = None
        self.stale_time = datetime.min
        self._on_signal = on_signal if on_signal is not None else self.on_signal
        self.subscribed = {id:False for id in self.algo_ids}
        self.subscription_deadline = datetime.min

    def on_signal(self, id:int, info:dict) -> None:
        """
        :param id: integer ID of the signal
        :param info:
           {
              "close":[
                 {"price":216.08,"side":"buy","symbol":"VRTX","timestamp":"2021-04-26 T14:30:00"},
                 {"price":87.1,"side":"sell","symbol":"MU","timestamp":"2021-04-26T14:30:00"},
                 {"price":80.28,"side":"sell"," symbol":"BMRN","timestamp":"2021-04-26T14:30:00"}
              ],
              "open":[
                 {"price":168.56,"side":"buy","symbol":"JBHT","timestamp":"2021-04-26T14:30:01"},
                 {"price":414.6,"side":"buy","symbol":"ILMN","timestamp":"2021-04-26T14:30:01"},
                 {"price":142.94,"side":"sell","symbol":"CDNS","timestamp":"2021-04-26T14:30:01"}
              ]
           }
        :return: None
        """
        self.log.info(f'SIGNAL {id}:\n{info}')

    def state_INIT(self):
        self._close_ws()
        url = 'https://alphahub.us/api/v1/session'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        params = {'user[email]': self.email, 'user[password]': self.password}
        response = requests.post(url, params, headers=headers)
        response_json = response.json()
        self.log.debug(f'authentication response:\n{response_json}')
        data = response_json['data']
        self.token = data['token']
        self.renew_token = data['renew_token']
        self.expiry = datetime.now() + OAUTH_TIMEOUT
        self.state = 'AUTHENTICATED'

    async def state_AUTHENTICATED(self):
        url = f'wss://alphahub.us/socket/websocket?vsn=2.0.0&api_token={self.token}'
        self.ws = await websockets.connect(url)
        self.log.debug('websocket connected')
        self.state = 'CONNECTED'

    def state_CONNECTED(self):
        self.subscribed = {id:False for id in self.algo_ids}
        self.subscription_deadline = datetime.now() + SUBSCRIPTION_TIMEOUT
        self.state = 'SUBSCRIBING'

    async def state_SUBSCRIBING(self):
        while not all(self.subscribed.values()): # if not everything is subscribed yet
            for id in [id for id,subscribed in self.subscribed.items() if not subscribed]: # create a list because the subscribed dict changes
                self.log.debug(f'subscribing to algo {id}')
                msg = f'[null,null,"algorithms:{id}","phx_join",{{}}]'
                await self._send(msg)
                while True: # if another channel's message is interjected we might have to receive multiple times
                    await self._recv() # this will set the subscribed flag. if another message is interjected it's ok
                    if self.state != 'SUBSCRIBING':
                        return
                    elif self.subscribed[id]:
                        break
        self.log.debug('all algorithms are subscribed')
        self.state = 'RECEIVING'

    async def state_RECEIVING(self):
        await self._recv()
        now = datetime.now()
        if now >= self.stale_time:
            # if there hasn't been any communication within the heartbeat timeout, send a heartbeat
            self.state = 'STALE'

    async def state_STALE(self):
        self.log.debug('sending heartbeat')
        await self._send('[null,null,"phoenix","heartbeat",{}]') # app level ping
        self.state = 'RECEIVING'

    def state_RECONNECTING(self):
        self._close_ws()
        if datetime.now() < self.expiry:
            self.log.debug('OAuth token still valid. Reconnecting websocket with existing token.')
            self.state = 'AUTHENTICATED'
        else:
            self.log.debug('OAuth token expired. Reauthenticating to reconnect websocket.')
            self.state = 'INIT'

    async def state_ERROR(self):
        await super().state_ERROR()
        self._close_ws()

    async def _send(self,msg):
        self.stale_time = datetime.now() + HEARTBEAT_INTERVAL
        self.log.debug(f'sending:\n{msg}')
        await self.ws.send(msg)

    async def _recv(self):
        try:
            msg = await wait_for(self.ws.recv(), RECEIVE_TIMEOUT_SECONDS)
        except asyncio.exceptions.TimeoutError:
            return
        except websockets.exceptions.ConnectionClosedError:
            raise ExceptionState('RECONNECTING')

        self.log.debug(f'received:\n{msg}')
        a,b,channel,method,info = json.loads(msg)

        # Algorithm channels
        if channel.startswith('algorithms:'):
            algo_id = int(channel[len('algorithms:'):])
            if method == 'phx_reply':
                if info['status'] == 'ok':
                    self.subscribed[algo_id] = True
                else:
                    self.log.warning(f'bad reply from subscription request:\n{msg}')
                    raise ExceptionState('ERROR')
            elif method == 'new_signals':
                asyncio.create_task(self._handle_signal_message(algo_id,info))
            elif method == 'phx_close':
                pass
            else:
                self.log.warning(f'unknown method on algorithms channel:\n{msg}')

        # system channel
        elif channel == 'phoenix':
            if method == 'phx_reply':
                if info['status'] != 'ok':
                    logging.warning(f'bad response from heartbeat:\n{msg}')
                    raise ExceptionState('ERROR')
                else:
                    self.log.debug('heartbeat ok')
            else:
                logging.warning(f'unknown method on phoenix channel:\n{msg}')

        # unknown channel
        else:
            self.log.warning(f'unknown channel:\n{msg}')

    async def _handle_signal_message(self, algo_id, info):
        if inspect.iscoroutinefunction(self._on_signal):
            # noinspection PyUnresolvedReferences
            await self._on_signal(algo_id,info)
        else:
            self._on_signal(algo_id,info)

    def _close_ws(self):
        if self.ws is not None:
            self.log.debug('closing ws connection')
            asyncio.create_task(self.ws.close())
            self.ws = None

