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

from ._fsm import SimpleFSM

OAUTH_TIMEOUT = timedelta(minutes=25)
HEARTBEAT_INTERVAL = timedelta(seconds=15)
HEARTBEAT_CHECK_SECONDS = HEARTBEAT_INTERVAL.total_seconds() / 2


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

        if on_signal is not provided, then this class's on_signal() method is invoked instead.

        :param log: an optional logging.Logger used by this class. If not given, then logs are published to
            alphahub.Connection
        """
        super().__init__(log=log)
        self.email = email
        self.password = password
        self.algo_ids = algo_ids
        self.token:Optional[str] = None
        self.renew_token:Optional[str] = None
        self.expiry:datetime = datetime.min
        self.ws:Optional[websockets.protocol.WebSocketCommonProtocol] = None
        self.stale_time = datetime.min
        self._on_signal = on_signal if on_signal is not None else self.on_signal

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

    async def state_CONNECTED(self):
        for id in self.algo_ids:
            msg = f'[null,null,"algorithms:{id}","phx_join",{{}}]'
            self.log.debug(f'sending "{msg}"')
            await self._send(msg)
            response = await self._recv()
            data = json.loads(response)
            if data[3] != 'phx_reply' or data[4]['status'] != 'ok':
                self.log.error(f'Bad response from subscription to signal id {id}:\n{response}')
                self.state = 'ERROR'
                return
        self.state = 'RECEIVING'

    async def _send(self,msg):
        self.stale_time = datetime.now() + HEARTBEAT_INTERVAL
        self.log.debug(f'sending:\n{msg}')
        await self.ws.send(msg)

    async def _recv(self):
        msg = await self.ws.recv()
        self.log.debug(f'received:\n{msg}')
        return msg

    async def state_RECEIVING(self):
        try:
            msg = await wait_for(self._recv(), HEARTBEAT_CHECK_SECONDS)
            data = json.loads(msg)
            if data[3] != 'new_signals':
                self.log.warning(f'expected signal message but received:\n{msg}')
                self.state = 'ERROR'
                return
            id = int(data[2].split[':'][1])
            info = data[3]
            if inspect.iscoroutinefunction(self._on_signal):
                # noinspection PyUnresolvedReferences
                await self._on_signal(id,info)
            else:
                self._on_signal(id,info)
        except asyncio.exceptions.TimeoutError:
            pass
        finally:
            if datetime.now() >= self.stale_time:
                self.state = 'STALE'

    async def state_STALE(self):
        self.log.debug('sending heartbeat')
        await self._send('[null,null,"phoenix","heartbeat",{}]') # app level ping
        await self._recv()
        self.state = 'RECEIVING'

    def _close_ws(self):
        if self.ws is not None:
            self.log.debug('closing ws connection')
            asyncio.create_task(self.ws.close())
            self.ws = None

    async def state_ERROR(self):
        await super().state_ERROR()
        self._close_ws()

def state_DONE(self):
        self.stop()
