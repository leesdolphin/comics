import asyncio
from collections import namedtuple, OrderedDict
import logging
import mimetypes
import os

import bs4
from jinja2 import FileSystemLoader, Environment
from yaml import add_representer, safe_load, safe_dump, SafeDumper

from comic.utils import mkdir
from comic.exception import SkipComicError
from comic.objects import FutureList, Comic, Client2

log = logging.getLogger(__name__)

this_dir = os.path.abspath(os.path.dirname(__file__))
loader = FileSystemLoader([os.path.join(this_dir, 'templates/'), os.path.abspath(os.path.join(this_dir, '../templates/'))])
environment = Environment(loader=loader)

load_limit = asyncio.Semaphore(25)


class ComicSite():

    def __init__(self, comic_info, comics, images):
        self.comic_info = comic_info
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
            raise ValueError('Set the config_file attribute before trying to save.')
        self.sort_comics()
        with open(self.config_file, 'w') as f:
            safe_dump({'comics': self.comics, 'images': self.images}, f)

    async def save_html(self, location):
        template_path = self.comic_info.get('template', 'base.html')
        template = environment.get_template(template_path)
        with open(location, 'w') as f:
            print(location)
            f.write(template.render(comic_info=self.comic_info, comics=self.comics, images=self.images))


class ComicDownloader:

    def __init__(self, parser, metadata):
        self.parser = parser
        self.comic_site = ComicSite(metadata, {}, {})
        self.folder = metadata['folder']
        self.base_folder = os.path.abspath(os.path.join(this_dir, '../', self.folder))
        mkdir(self.base_folder)
        self.images_folder = 'images/'
        mkdir(os.path.join(self.base_folder, self.images_folder))
        self.config_file = os.path.join(self.base_folder, '.data.yaml')
        self.initialurl = metadata['initialurl']
        self.db = {}

    async def load_existing_comics(self):
        try:
            with open(self.config_file) as f:
                existing_data = safe_load(f)
                if existing_data and existing_data.get('comics'):
                    existing_comics = existing_data['comics']
                    for comic_id, comic in sorted(existing_comics.items()):
                        self.comic_site.set_comic(comic_id, Comic(**comic))
                if existing_data and existing_data.get('images'):
                    existing_images = existing_data['images']
                    for image_url, image_path in existing_images.items():
                        self.comic_site.set_image(image_url, image_path)
        except:
            log.exception('Exception occoured whilst loading file %s. Ignoring file.', self.config_file)
        self.comic_site.config_file = self.config_file
        return self.comic_site

    async def get_current_comic(self, client):
        last_id, last_comic = self.comic_site.last_entry
        if last_comic:
            current_id = last_id + 1
            if not last_comic.next:
                last_comic = await self.load_comic(client, last_comic.origin)
                if last_comic.next:
                    self.comic_site.set_comic(last_id, last_comic)
                    self.comic_site.save()
            current_url = last_comic.next
        else:
            current_url = self.initialurl
            current_id = 1
        return current_id, current_url

    async def load_comics(self):
        pending_futures = FutureList()
        await self.load_existing_comics()
        await self.comic_site.save_html(os.path.join(self.base_folder, 'index.html'))
        last_id_in_file = None
        try:
            with Client2(self.comic_site.comic_info['name'], skip_auto_headers=['User-Agent']) as client:
                if self.comic_site and last_id_in_file is None:
                    pending_futures.add(self.check_existing_comics(client))
                current_id, current_url = await self.get_current_comic(client)
                if last_id_in_file is None:
                    last_id_in_file = current_id
                while current_url is not None:
                    try:
                        comic = await self.load_comic(client, current_url)
                    except SkipComicError as skip:
                        current_url = skip.comic.next
                        continue
                    print(current_id, comic)
                    pending_futures.add(self.download_comic(client, current_id, comic))
                    self.comic_site.set_comic(current_id, comic)
                    current_url = comic.next
                    current_id += 1
                    await self.comic_site.save()
                    ## Download in 50-long blocks.
                    if not comic.next or current_id > last_id_in_file + 1000:
                        break
                log.info("Done loading information. Waiting on images.")
                await pending_futures
        except:
            log.exception("load_comics failed.")
            await self.comic_site.save()
            raise
        else:
            await self.comic_site.save()
            await self.comic_site.save_html(os.path.join(self.base_folder, 'index.html'))

    async def check_existing_comics(self, client):
        image_downloads = FutureList()
        try:
            for comic_id, comic in self.comic_site.comics.items():
                if not await self.check_comic(client, comic_id, comic):
                    image_downloads.add(self.download_comic(client, comic_id, comic))
        except:
            log.exception("check_existing_comics failed.")
            await self.comic_site.save()
            raise
        else:
            try:
                await image_downloads
            finally:
                await self.comic_site.save()
            await self.comic_site.save_html(os.path.join(self.base_folder, 'index.html'))

    async def load_comic(self, client, url):
        async with load_limit:
            async with await client.get(url) as response:
                content = await response.text()
        try:
            return self.parser.load_comic(url, content)
        except:
            log.exception("load_comic failed. Called with url=%s", url)
            raise

    async def comic_info(self, client, comic, comic_id):
        image_url = comic.image_url
        if not image_url:
            raise ValueError('No image for comic: %r' % (comic, ))
        image_extn = await self.guess_type(client, image_url)
        image_path = os.path.join(self.images_folder, 'comic-%d' % (comic_id,))
        image_full_path = os.path.join(self.base_folder, image_path)
        return image_extn, image_path, image_full_path

    async def check_comic(self, client, comic_id, comic):
        image_url = comic.image_url
        image_extn, image_path, image_full_path = await self.comic_info(client, comic, comic_id)
        image_path_ext = image_path + image_extn
        image_full_path_ext = image_full_path + image_extn
        existing_path = self.comic_site.get_image(image_url)
        if existing_path and existing_path.startswith(image_path) and os.path.isfile(image_full_path_ext):
            ## Exists so ignore
            return True
        if os.path.isfile(image_full_path_ext):
            self.comic_site.set_image(image_url, image_path_ext)
            return True
        return False

    async def guess_type(self, client, image_url, r=None):
        if r is not None and 'Content-Type' in r.headers and r.headers['Content-Type']:
            mimetype = r.headers['Content-Type']
        else:
            mimetype, _ = mimetypes.guess_type(image_url)
            if mimetype is None and r is None:
                async with client.head(image_url) as req:
                    return await self.guess_type(client, image_url, req)
            elif mimetype is None:
                raise ValueError("Cannot determine mimetype for URL: %s -- %r" % (image_url, r.headers))
        log.info('XX %s %s', image_url, mimetype)
        all_types = mimetypes.guess_all_extensions(mimetype, strict=True)
        for bad_type in ['.jpe']:
            if bad_type in all_types:
                all_types.remove(bad_type)
        all_types.sort()
        if all_types:
            return all_types[0]
        else:
            _, _, image_extn = comic.image_url.rpartition('.')
            if image_extn:
                return '.' + image_extn
            else:
                raise ValueError("Cannot find image extension for URL: %s" % (image_url, ))

    async def download_comic(self, client, comic_id, comic):
        try:
            if await self.check_comic(client, comic_id, comic):
                return
            image_url = comic.image_url
            image_extn, image_path, image_full_path = await self.comic_info(client, comic, comic_id)
            image_path_ext = image_path + image_extn
            image_full_path_ext = image_full_path + image_extn

            async with load_limit:
                print("Downloading %s into %s" % (image_url, image_path_ext))
                async with client.get(image_url) as r:
                    with open(image_full_path_ext, 'wb') as f:
                        f.write(await r.read())
                    print("Downloaded  %s into %s" % (image_url, image_path_ext))
            self.comic_site.set_image(image_url, image_path)
            await self.comic_site.save()
        except:
            log.exception("download_comic failed for cid=%s; comic=%r", comic_id, comic )
            raise
