import asyncio
from collections import namedtuple, OrderedDict
import logging

import aiohttp
from yaml import add_representer, SafeDumper


log = logging.getLogger(__name__)



Comic = namedtuple('Comic', 'origin, image_url, description, title, next, prev')
add_representer(Comic, lambda dumper, comic: dumper.represent_dict(comic._asdict()), Dumper=SafeDumper)
add_representer(OrderedDict, lambda dumper, odict: dumper.represent_dict(odict), Dumper=SafeDumper)


class Client2(aiohttp.ClientSession):

    def __init__(self, name, *a, **k):
        if 'connector' in k:
            raise ValueError("Does not support specifying a connector.")
        super().__init__(*a, **k)
        self.__max_retries = k.get('max_retries', 5)
        self.__name = name
        self.__closed = False

    def close(self):
        self.__closed = True
        # log.info("Closing session %s", self.__name, stack_info=True)
        super().close()

    def __del__(self):
        # log.info("Deleting session %s", self.__name, stack_info=True)
        super().__del__(self)

    @asyncio.coroutine
    def _request(self, *a, **k):
        e = None
        retry = 0
        while retry < self.__max_retries:
            retry += 1
            try:
                return (yield from super()._request(*a, **k))
            except aiohttp.ClientResponseError as e:
                log.exception("Retry %d failed to %s %s", retry, *a[0:2])
                ## Damn thing closed the connector.
                if retry >= self.__max_retries:
                    raise e
                else:
                    self.reopen()
            log.info
        ## Should never reach here
        raise e or ValueError("Retries expired!")

    def reopen(self):
        if self.__closed:
            raise ValueError("Cannot reopen session that has been closed using the close() method.")
        if self.closed:
            self._connector = aiohttp.TCPConnector(loop=self._loop)


class FutureList(list):

    def add(self, coro):
        self.append(asyncio.ensure_future(coro))

    def __await__(self):
        ## Need to use yield from syntax as __await__ cannot be a coroutine
        if self:
            return (yield from asyncio.wait(self))

    async def __aiter__(self):
        return FutureAIter(self)

    def as_completed(self):
        return asyncio.as_completed(self)


class FutureAIter():

    def __init__(self, items):
        self._items = asyncio.as_completed(items)

    async def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            next_future = next(self._items)
        except StopIteration:
            raise StopAsyncIteration
        return await next_future
