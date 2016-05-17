import asyncio
import logging

from yaml import safe_load

from comic.objects import Client2
from comic.parsers import ComicParser
from comic.exception import MissingElementError, SkipComicError
from comic.utils import remove_fragment


log = logging.getLogger(__name__)


class ComicGuesser():

    def __init__(self, name, comic_url, base_classes, mixins):
        self._name = name
        self._comic_url = comic_url
        self._base_classes = base_classes
        self._mixins = mixins

    @asyncio.coroutine
    def __await__(self):
        yield from self.find()

    async def find(self):
        url = self._comic_url
        with Client2(self._name, skip_auto_headers=['User-Agent']) as client:
            async with client.get(url) as response:
                content = await response.text()
            base_name, parser, comic = await self.find_base_class(url, content)
            if base_name:
                ## Found something.
                async with client.get(comic.next) as response:
                    content = await response.text()
                comic2 = parser.load_comic(comic.next, content)
                print(comic)
                print(comic2)
                if remove_fragment(url) == remove_fragment(comic2.prev):
                    print("URL check passed")
                    return base_name, comic, comic2
                else:
                    print("URL check passed failed %s %s" % (remove_fragment(url), remove_fragment(comic2.prev)))
        return None, None, None

    async def find_base_class(self, url, content):
        for base_name in self._base_classes:
            parser = ComicParser.load_parser({'base': base_name}, self._base_classes, self._mixins)
            log.info("%s -> %r", base_name, parser)
            try:
                comic = parser.load_comic(url, content)
                if comic.next is not None:
                    return base_name, parser, comic
            except (SkipComicError, MissingElementError) as e:
                # log.exception("%s", base_name)
                pass
        return None, None, None
