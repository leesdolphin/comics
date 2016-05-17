import logging
import re

import bs4

from comic.utils import dict_merge, resolve_url
from comic.objects import Comic
from comic.exception import MissingElementError, SkipComicError

log = logging.getLogger(__name__)


def html_to_text(tag, include_line_breaks=False):
    return re.sub(r'\s\s+', ' ', tag.get_text())


def html_to_safer_html(tag):
    if not any(tag.stripped_strings):
        return ""
    for unsafe_selector in ['iframe', 'script', 'link', '.twitterbutton', '.clear', '.ssba', '.attachment-full']:
        for unsave_tag in tag.select(unsafe_selector):
            unsave_tag.decompose()
    for s_ in tag.parents:
        ## last is None, 2nd last is the BeautifulSoup Object.
        if s_ is not None:
            soup = s_
    ## Change the outside tag to a span.
    span = tag.wrap(soup.new_tag('span'))
    tag.unwrap()
    return span.prettify(formatter="html")


class ComicParser():

    @classmethod
    def load_parser(cls, info, all_bases, all_mixins):
        comic_info = cls.parse_comic(info, all_bases, all_mixins)
        try:
            return cls(comic_info)
        except:
            log.exception("Failed to create parser using %r", comic_info)
            log.error("Input info: %r", info)
            raise

    @classmethod
    def parse_comic(cls, info, all_bases, all_mixins):
        base = {}
        mixin = {}
        if 'base' in info:
            base = cls.load_base(info['base'], all_bases, all_mixins)
        if 'mixins' in info:
            mixin = cls.load_mixins(info['mixins'], all_mixins)
        return dict_merge(base, mixin, info)

    @classmethod
    def load_base(cls, base_name, all_bases, all_mixins):
        previous_bases = []
        base = {}
        while base_name is not None:
            if base_name in previous_bases:
                raise ValueError("Cyclic bases detected: %s -> %s" % (' -> '.join(previous_bases), base_name))
            previous_bases.append(base_name)
            base.pop('base', None)
            this_base = all_bases[base_name]
            if 'mixins' in this_base:
                mixins = cls.load_mixins(this_base['mixins'], all_mixins)
                mixins.pop('base', None)
                this_base = dict_merge(this_base, mixins)
            base = dict_merge(this_base, base)
            base_name = base.get('base')
        return base

    @classmethod
    def load_mixins(cls, mixin_list, all_mixins):
        if isinstance(mixin_list, str):
            ## Single mixin can just be a string. Convert to single item list.
            mixin_list = [mixin_list]
        mixin = {}
        for mixin_name in mixin_list:
            mixin = dict_merge(mixin, all_mixins[mixin_name])
        return mixin

    def __init__(self, comic_info):
        includealt = comic_info.get('includealt', True)
        self.parsers = [
            ElementTextParser(comic_info['title'], 'title', ignore_missing=False),
            ComicImageParser(comic_info['image'], includealt=includealt),
            LinkParser(comic_info['links']['prev'], 'prev', allow_missing=True),
            LinkParser(comic_info['links']['next'], 'next', allow_missing=True),
        ]
        if not includealt:
            if comic_info['description'].lower() != '!!empty!!':
                self.parsers.append(ElementTextParser(comic_info['description'], 'description', raw_html=True))

    def load_comic(self, url, content):
        soup = bs4.BeautifulSoup(content, 'html.parser')
        comic = Comic(url, None, None, None, None, None)
        skip_comic = False
        for parser in self.parsers:
            try:
                comic = parser.update_comic(url, soup, comic)
            except MissingElementError as e:
                print('Failed to load required element from %s' % (url))
                print('Using %s parser.' % (parser))
                raise
            except SkipComicError:
                skip_comic = True
        if skip_comic:
            raise SkipComicError(comic)
        return comic


class ElementParser():

    def update_comic(self, url, soup, comic):
        return comic

    def __repr__(self):
        return "%s(**%r)" % (self.__class__.__qualname__, self.__dict__)


class ElementTextParser(ElementParser):

    def __init__(self, selector, dest, ignore_missing=True, raw_html=False):
        self.selector, _, self.attribute = selector.partition('!')
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
        tag = tags[0]
        if self.attribute:
            if self.attribute in tag.attrs or self.ignore_missing:
                content = tag.get(self.attribute, '')
            elif self.attribute not in tag and not self.ignore_missing:
                print(self.attribute, tag)
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
        href = tags[0].get('href', '#')
        if href.startswith('javascript'):
            ## We can't handle javascript links.
            return comic
        link_url = resolve_url(url, href)
        if url == link_url:
            ## The link leads nowhere.
            return comic
        return comic._replace(**{self.dest: link_url})
