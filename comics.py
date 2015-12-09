import asyncio
from collections import namedtuple, OrderedDict
from contextlib import closing
import logging
import mimetypes
import os
from urllib.parse import urljoin, urldefrag

import bs4
from yaml import add_representer, safe_load, safe_dump, SafeDumper
import aiohttp

FILE = "comics.yaml"

log = logging.getLogger(__name__)


def remove_fragment(url):
    return urldefrag(url)[0]


def resolve_url(base, url):
    if url is None:
        return None
    return remove_fragment(urljoin(base, url))


def html_to_text(tag, include_line_breaks=False):
    if not include_line_breaks:
        return str(tag.get_text())


def html_to_safer_html(tag):
    return tag.prettify()


def to_folder_name(name):
    return name


def dict_merge(*dicts):
    if not dicts:
        return {}
    output = {}
    for dictionary in dicts:
        for key, value in dictionary.items():
            if isinstance(value, dict):
                ## TODO deep merge
                output[key] = dict_merge(output.get(key, {}), value)
            else:
                output[key] = value
    return output

Comic = namedtuple('Comic', 'origin, image_url, description, title, next, prev')
add_representer(Comic, lambda dumper, comic: dumper.represent_dict(comic._asdict()), Dumper=SafeDumper)
add_representer(OrderedDict, lambda dumper, odict: dumper.represent_dict(odict), Dumper=SafeDumper)


class ComicSite():

    def __init__(self, comics, images):
        self.comics = OrderedDict(sorted(comics.items()))
        self.images = dict(images)
        self.config_file = None

    def set_comic(self, comic_id, new_comic):
        self.comics[comic_id] = new_comic

    def sort_comics(self):
        self.comics = OrderedDict(sorted(self.comics.items()))

    def set_image(self, image_url, image_path):
        self.images[image_url] = image_path

    def get_image(self, image_url):
        return self.images.get(image_url)

    @property
    def last_id(self):
        return next(iter(reversed(self.comics.keys()))) if self.comics else 0

    @property
    def last_comic(self):
        return next(iter(reversed(self.comics.values()))) if self.comics else None

    @property
    def last_entry(self):
        return (self.last_id, self.last_comic)

    async def save(self):
        if self.config_file is None:
            raise ValueError("Set the config_file attribute before trying to save.")
        self.sort_comics()
        with open(self.config_file, 'w') as f:
            safe_dump({'comics': self.comics, 'images': self.images}, f)


class FutureList(list):

    def add(self, coro):
        self.append(asyncio.async(coro))

    def __await__(self):
        ## Need to use yield from syntax as __await__ cannot be a coroutine
        if self:
            yield from asyncio.wait(self)


class MissingElementError(Exception):
    pass


class SkipComicError(Exception):

    def __init__(self, comic=None, *a, **kw):
        super().__init__(comic, *a, **kw)
        self.comic = comic


class ElementParser():

    def update_comic(self, url, soup, comic):
        return comic

    def __repr__(self):
        return "%s(**%r)" % (self.__class__.__qualname__, self.__dict__)


class ElementTextParser(ElementParser):

    def __init__(self, selector, dest, ignore_missing=True, raw_html=False):
        self.selector = selector
        self.dest = dest
        self.ignore_missing = ignore_missing
        self.raw_html = raw_html

    def update_comic(self, url, soup, comic):
        tags = soup.select(self.selector)
        if not tags:
            if self.ignore_missing:
                return comic._replace(**{self.dest: ''})
            else:
                raise MissingElementError(self, soup)
        if self.raw_html:
            content = html_to_safer_html(tags[0])
        else:
            content = html_to_text(tags[0])
        return comic._replace(**{self.dest: content})


class ComicImageParser(ElementParser):

    def __init__(self, selector, *, includealt=True):
        self.selector = selector
        self.includealt = includealt

    def update_comic(self, url, soup, comic):
        image = soup.select(self.selector)
        if not image:
            raise SkipComicError(comic, self, soup)
        image = image[0]
        img_src = resolve_url(url, image['src'])
        replacement = {'image_url': img_src}
        if self.includealt:
            replacement['description'] = image.get('title', image.get('alt', None))
        return comic._replace(**replacement)


class LinkParser(ElementParser):

    def __init__(self, selector, dest, allow_missing=False):
        self.selector = selector
        self.dest = dest
        self.allow_missing = allow_missing

    def update_comic(self, url, soup, comic):
        tags = soup.select(self.selector)
        if not tags:
            if not self.allow_missing:
                raise MissingElementError(self, soup)
            else:
                return comic
        url = resolve_url(url, tags[0].get('href'))
        return comic._replace(**{self.dest: url})


class Parser():

    def __init__(self, folder, initialurl, links, image, title, includealt=True,  **kwargs):
        print(kwargs)
        self.parsers = [
            ElementTextParser(title, 'title', ignore_missing=False),
            ComicImageParser(image, includealt=includealt),
            LinkParser(links['prev'], 'prev', allow_missing=True),
            LinkParser(links['next'], 'next', allow_missing=True),
        ]
        if not includealt:
            self.parsers.append(ElementTextParser(kwargs['description'], 'description', raw_html=True))
        self.folder = folder
        self.initialurl = initialurl
        self.db = {}

    async def load_existing_comics(self, config_file):
        comics = ComicSite({}, {})
        try:
            with open(config_file) as f:
                existing_data = safe_load(f)
                if existing_data and existing_data.get('comics'):
                    existing_comics = existing_data['comics']
                    for comic_id, comic in sorted(existing_comics.items()):
                        comics.set_comic(comic_id, Comic(**comic))
                if existing_data and existing_data.get('images'):
                    existing_images = existing_data['images']
                    for image_url, image_path in existing_images.items():
                        comics.set_image(image_url, image_path)
        except:
            log.exception("Exception occoured whilst loading file %s. Ignoring file.", config_file)
        comics.config_file = config_file
        return comics

    async def get_current_comic(self, comics, client):
        last_id, last_comic = comics.last_entry
        if last_comic:
            current_id = last_id + 1
            if not last_comic.next:
                last_comic = await self.load_comic(client, last_comic.origin)
                if last_comic.next:
                    comics.set_comic(last_id, last_comic)
                    comics.save()
            current_url = last_comic.next
        else:
            current_url = self.initialurl
            current_id = 1
        return current_id, current_url

    async def load_comics(self):
        full_folder = os.path.abspath(os.path.join('comics/', self.folder))
        try:
            os.mkdir(full_folder)
        except FileExistsError:
            pass
        images_folder = os.path.join(full_folder, 'images/')
        try:
            os.mkdir(images_folder)
        except FileExistsError:
            pass
        config_file = os.path.join(full_folder, '.data.yaml')
        pending_futures = FutureList()
        comics = await self.load_existing_comics(config_file)

        with closing(aiohttp.ClientSession(skip_auto_headers=['User-Agent'])) as client:
            if comics:
                pending_futures.add(self.check_existing_comics(client, images_folder, comics))
            try:
                current_id, current_url = await self.get_current_comic(comics, client)
                while current_url is not None:
                    try:
                        comic = await self.load_comic(client, current_url)
                    except SkipComicError as skip:
                        current_url = skip.comic.next
                        continue
                    print(current_id, comic)
                    pending_futures.add(self.download_comic(client, images_folder, comics, current_id, comic))
                    comics.set_comic(current_id, comic)
                    current_url = comic.next
                    current_id += 1
                    if current_id > 50:
                        break
            finally:
                await pending_futures
                await comics.save()

    async def check_existing_comics(self, client, images_folder, comics):
        image_downloads = FutureList()
        try:
            for comic_id, comic in comics.comics.items():
                image_downloads.add(self.download_comic(client, images_folder, comics, comic_id, comic))
        finally:
            await image_downloads
            await comics.save()

    async def load_comic(self, client, url, directions=('next', 'prev')):
        async with client.get(url) as response:
            content = await response.text()
        print("Loaded", self.folder)
        soup = bs4.BeautifulSoup(content, "html.parser")
        print("Souped", self.folder)
        comic = Comic(url, None, None, None, None, None)
        skip_comic = False
        for parser in self.parsers:
            try:
                comic = parser.update_comic(url, soup, comic)
            except MissingElementError as e:
                print("Failed to load required element from %s" % (url))
                print("Using %s parser." % (parser))
                print(e)
            except SkipComicError:
                skip_comic = True
        if skip_comic:
            raise SkipComicError(comic)
        return comic

    async def download_comic(self, client, image_folder, comics, comic_id, comic):
        image_url = comic.image_url
        image_path = os.path.join(image_folder, "comic-%d" % (comic_id,))
        if not image_url:
            raise ValueError("No image for comic: %r" % (comic, ))
        existing_path = comics.get_image(image_url)
        if existing_path and existing_path.startswith(image_path) and os.path.isfile(existing_path):
            ## Exists so ignore
            return
        async with client.get(image_url) as r:
            extn = None
            if 'Content-Type' in r.headers:
                mimetype = r.headers['Content-Type']
            else:
                mimetype, _ = mimetypes.guess_type(image_url)
            all_types = mimetypes.guess_all_extensions(mimetype, strict=True)
            for bad_type in ['.jpe']:
                if bad_type in all_types:
                    all_types.remove(bad_type)
            all_types.sort()
            extn = all_types[0] if all_types else None
            image_path = image_path + extn
            print(image_path)
            with open(image_path, 'wb') as f:
                f.write(await r.read())
        comics.set_image(image_url, image_path)


async def main():
    comic_parsers = FutureList()
    with open(FILE) as f:
        comics_data = safe_load(f)
    comic_presets = comics_data.get('presets', {})
    comic_mixins = comics_data.get('mixins', {})
    comics = comics_data['comics']
    for name, comic in comics.items():
        comic_base = {}
        if 'base' in comic:
            preset = comic.pop('base')
            comic_base = dict_merge(comic_base, comic_presets.get(preset, {}))
        if 'mixins' in comic:
            mixins = comic.pop('mixins')
            print("Mixins", mixins)
            if isinstance(mixins, str):
                ## Single mixin can just be a string. Convert to single item list.
                mixins = [mixins]
            for mixin in mixins:
                comic_base = dict_merge(comic_base, comic_mixins.get(mixin, {}))
        ## Order of presidence: Base <- Mixins <- Comic
        ## Anything in Comic always overrides.
        comic = dict_merge(comic_base, comic)
        if comic.get('layout') not in ('horizontal', 'vertical', 'pane'):
            comic['layout'] = 'horizontal'
        if 'folder' not in comic:
            comic['folder'] = to_folder_name(name)
        print(name, comic)
        comic_parsers.add(Parser(**comic).load_comics())
    await comic_parsers

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
