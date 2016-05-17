import logging
import os
from urllib.parse import urljoin, urldefrag


log = logging.getLogger(__name__)


def remove_fragment(url):
    return urldefrag(url)[0]


def resolve_url(base, url):
    if url is None:
        return None
    return remove_fragment(urljoin(base, url))


def to_folder_name(name):
    return name


def mkdir(directory):
    try:
        os.mkdir(directory)
    except FileExistsError:
        pass


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
