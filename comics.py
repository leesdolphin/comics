import asyncio
from collections import namedtuple
from urllib.parse import urljoin, urldefrag

import bs4
import yaml
import aiohttp

FILE = "comics.yaml"

def remove_fragment(url):
    return urldefrag(url)[0]

def resolve_url(base, url):
    if url is None:
        return None
    return remove_fragment(urljoin(base, url))

def html_to_text(tag, include_line_breaks=False):
    if not include_line_breaks:
        return str(title[0].get_text())

Comic = namedtuple('Comic', 'origin, image_url, description, title, next, prev')


class MissingElementError(Exception):
    pass


class ElementParser():

    def update_comic(self, url, soup, comic):
        return comic

class ElementTextParser(ElementParser):

    def __init__(self, selector, dest):
        self.selector = selector
        self.dest = dest

    def update_comic(self, url, soup, comic):
        tags = soup.select(self.selector)
        if not tags:
            raise MissingElementError
        content = html_to_text(tags[0])
        return comic.replace(**{self.dest: content})

class ComicImageParser(ElementParser):

    def __init__(self, selector, *, includealt=True):
        self.selector = selector
        self.includealt = includealt

    def update_comic(self, url, soup, comic):
        image = soup.select(self.image)
        if not image:
            raise MissingElementError
        image = image[0]
        img_src = resolve_url(url, image['src'])
        replacement = {'image_url': img_src}
        if self.includealt:
            replacement['description'] = image.get('title', image.get('alt', None))
        return comic.replace(**replacement)


class Parser():

    def __init__(self, folder, initialurl, links, image, title, includealt=True, **kwargs):
        print(kwargs)
        self.parsers = [
            ElementTextParser(title, 'title'),
            ComicImageParser(image, includealt=includealt),
            LinkParser(links['prev'], 'prev', allow_missing=True),
            LinkParser(links['next'], 'next', allow_missing=True),
        ]
        if not includealt:
            self.parsers.append(ElementTextParser(kwargs['description'], 'description'))
        self.folder = folder
        self.initialurl = initialurl
        self.db = {}

    async def load_comics(self):
        client = aiohttp.ClientSession()
        try:
            await self.load_comic(client, self.initialurl)
        finally:
            client.close()

    async def load_comic(self, client, url, directions=('next', 'prev')):
        async with client.get(url) as response:
            soup = bs4.BeautifulSoup(await response.text(), "html.parser")
            comic = Comic(url, None, None, None, None, None)
            for parser in self.parsers:
                comic = parser.update_comic(url, soup, comic)
            print(comic)








async def main():
    comic_parsers = []
    with open(FILE) as f:
        comics = yaml.load(f)
    print(comics)
    for name, comic in comics.items():
        await Parser(**comic).load_comics()


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
