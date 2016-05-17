import asyncio
# from collections import namedtuple, OrderedDict
# from contextlib import closing
# import functools
import logging
# import mimetypes
import os
import signal

from yaml import safe_load

from comic.parsers import ComicParser
from comic.utils import to_folder_name
from comic.loader import ComicDownloader
from comic.objects import FutureList
from comic.guess import ComicGuesser


FILE = 'comics.yaml'

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

async def load_comics(comics, comic_presets, comic_mixins):
    comic_parsers = FutureList()
    for name, comic in comics.items():
        metadata = comic.get('meta', {})
        for meta_keys in ['name', 'layout', 'folder', 'initialurl']:
            if meta_keys in comic and meta_keys not in metadata:
                metadata[meta_keys] = comic[meta_keys]
        metadata.setdefault('name', name)
        if metadata.get('layout') not in ('horizontal', 'vertical', 'pane'):
            metadata['layout'] = 'horizontal'
        if 'folder' not in comic:
            metadata['folder'] = to_folder_name(name)
        parser = ComicParser.load_parser(comic, comic_presets, comic_mixins)
        comic_parsers.add(ComicDownloader(parser, metadata).load_comics())
    await comic_parsers


async def load_guesses(filename, comics_data):
    comic_presets = comics_data.get('presets', {})
    comic_mixins = comics_data.get('mixins', {})
    guess_comics = comics_data['guess_comics']
    if not guess_comics:
        return
    loaded_comic_data = {}
    failed_comics = {}

    comic_guessers = FutureList()
    comic_loaders = FutureList()
    for name, url in guess_comics.items():
        comic_guessers.add(load_guess_for(name, url, comic_presets, comic_mixins))
    async for name, data, comics in comic_guessers:
        print(name, data)
        if not data or not comics:
            print()
            print()
            failed_comics[name] = guess_comics[name]
            continue
        loaded_comic_data[name] = data
        parser = ComicParser.load_parser(data, comic_presets, comic_mixins)
        downloader = ComicDownloader(parser, data['meta'])
        await downloader.load_existing_comics()
        for comic_id, comic in comics.items():
            if comic_id not in downloader.comic_site.comics:
                downloader.comic_site.set_comic(comic_id, comic)
        await downloader.comic_site.save()
        comic_loaders.add(downloader.load_comics())
    comics_data['comics'] = dict(comics_data['comics']).update(loaded_comic_data)
    comics_data['failed_comics'] = failed_comics
    print(comics_data)
    await comic_loaders


async def load_guess_for(name, url, comic_presets, comic_mixins):
    guesser = ComicGuesser(name, url, comic_presets, comic_mixins)
    base_name, comic, comic2 = await guesser.find()
    if not base_name:
        return name, None, None
    comics = {
        1: comic, 2: comic2
    }
    data = {
        'base': base_name,
        'meta': {
            'name': name,
            'folder': to_folder_name(name),
            'layout': 'horizontal',
            'initialurl': url,
        }
    }
    return name, data, comics



async def main():
    pending_tasks = FutureList()
    with open(FILE) as f:
        comics_data = safe_load(f)
    comic_presets = comics_data.get('presets', {})
    comic_mixins = comics_data.get('mixins', {})
    comics = comics_data['comics']
    pending_tasks.add(load_comics(comics, comic_presets, comic_mixins))
    pending_tasks.add(load_guesses(FILE, comics_data))
    # for name, url in
    await pending_tasks


def cancel_all_tasks():
    tasks = asyncio.Task.all_tasks(asyncio.get_event_loop())
    pending = FutureList()
    for task in tasks:
        task.cancel()
        pending.add(task)
    asyncio.async(pending)


def list_all_tasks():
    tasks = asyncio.Task.all_tasks(asyncio.get_event_loop())
    pending = FutureList()
    for task in tasks:
        if task.done():
            continue
        print()
        print(task)
        task.print_stack()
        print()


loop = asyncio.get_event_loop()
# loop.set_debug(True)
main = asyncio.ensure_future(main())
loop.add_signal_handler(signal.SIGHUP, list_all_tasks)
loop.add_signal_handler(signal.SIGINT, main.cancel)


try:
    loop.run_until_complete(main)
except:
    log.exception('Main failed')
    pass
print("MAIN COMPLETE")
def check_task(task):
    return task.done() and not task.cancelled() and not task.exception()
tasks = asyncio.Task.all_tasks(asyncio.get_event_loop())
retrieved_tasks = set()
while any((not check_task(task) for task in tasks)):
    task_list = FutureList((task for task in tasks if not check_task(task)))
    log.info("Pending Loop. Has %d items", len(task_list))
    for task in task_list.as_completed():
        try:
            loop.run_until_complete(task)
        except:
            log.exception('Task raised exception %r', task)
            pass
    retrieved_tasks = retrieved_tasks | tasks
    tasks = asyncio.Task.all_tasks(asyncio.get_event_loop()) - retrieved_tasks
    log.info("Pending Loop. Has %d new items", len([task for task in tasks if not check_task(task)]))


loop.close()
