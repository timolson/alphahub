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

from _fsm import SimpleFSM

OAUTH_TIMEOUT = timedelta(minutes=25)
HEARTBEAT_INTERVAL = timedelta(seconds=15)
HEARTBEAT_CHECK_SECONDS = HEARTBEAT_INTERVAL.total_seconds() / 2


class Connection (SimpleFSM):
    def __init__(self, email:str, password:str, algo_ids:Iterable[int], *,
                 on_message:Optional[Callable[[str],None]]=None, log:logging.Logger=None):
        super().__init__(log=log)
        self.email = email
        self.password = password
        self.algo_ids = algo_ids
        self.token:Optional[str] = None
        self.renew_token:Optional[str] = None
        self.expiry:datetime = datetime.min
        self.ws:Optional[websockets.protocol.WebSocketCommonProtocol] = None
        self.stale_time = datetime.min
        self._on_message = on_message if on_message is not None else self.on_message

    def on_message(self, msg:str):
        self.log.info(f'SIGNAL:\n{msg}')

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
            if inspect.iscoroutinefunction(self._on_message):
                # noinspection PyUnresolvedReferences
                await self._on_message(msg)
            else:
                self._on_message(msg)
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
