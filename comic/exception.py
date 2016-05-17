
class MissingElementError(Exception):
    pass


class SkipComicError(Exception):

    def __init__(self, comic=None, *a, **kw):
        super().__init__(comic, *a, **kw)
        self.comic = comic
