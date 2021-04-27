__all__ = ['SimpleFSM','ExceptionState']

import inspect
import logging
from asyncio import sleep
from typing import Optional

DEFAULT_ERROR_WAIT_TIME=5

class ExceptionState (Exception):
    """
    If this class is raised, the state machine quietly and immediately jumps to the new state.  Use this to force
    state changes from within subroutines that would otherwise exit normally and keep the current state.
    """
    def __init__(self,state):
        self.state = state

class SimpleFSM:
    def __init__(self, *, log=None):
        self.log:logging.Logger = log if log is not None else logging.getLogger(f'alphahub.{self.__class__.__name__}')
        self.last_exception:Optional[Exception] = None
        self.running = False
        self.state = 'INIT'

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self,value:str):
        self.log.debug(f'Entering state {value}')
        self._state = value

    async def start(self):
        if self.running:
            return
        self.running = True
        while self.running:
            func = getattr(self,f'state_{self.state}',None)
            if func is None:
                self.fatal(f'No method found for state {self.state}')
            else:
                try:
                    if inspect.iscoroutinefunction(func):
                        await func()
                    else:
                        func()
                    self.last_exception = None
                except ExceptionState as e:
                    self.last_exception = None
                    self.state = e.state
                except Exception as e:
                    self.last_exception = e
                    self.state = 'ERROR'
        self.log.debug('stopped')

    def stop(self):
        self.running = False

    def state_INIT(self):
        pass

    async def state_ERROR(self):
        self.log.exception(self.last_exception,exc_info=self.last_exception)
        await sleep(DEFAULT_ERROR_WAIT_TIME)
        self.state = 'INIT' # return to INIT state by default

    def fatal(self,msg,exception=None):
        self.log.critical(msg,exc_info=exception)
        self.stop()
