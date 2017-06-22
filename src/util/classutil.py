import itertools as it

_object_id = it.count().__next__

class IDObject:
    def __init__(self):
        self._id = _object_id()

    def __eq__(self, other):
        return isinstance(other, type(self)) and self._id == other._id

    def __ne__(self, other):
        return not isinstance(other, type(self)) or not self._id == other._id

    def __hash__(self):
        return hash(self._id)

    def __repr__(self):
        return "<{}.{} : {}>".format(
                self.__class__.__module__,
                self.__class__.__name__,
                self.id,
                )

    @property
    def id(self):
        return self._id

class NamedIDObject(IDObject):
    def __init__(self, name, formatter=None):
        super().__init__()
        self._name = '{}'.format(name)
        if formatter is not None:
            self._name = formatter(self)

    def __repr__(self):
        return "<{}.{} : '{}' : {}>".format(
                self.__class__.__module__,
                self.__class__.__name__,
                self.name,
                self.id,
                )

    @property
    def name(self):
        return self._name

class FlyWeightMeta(type):
    '''
        FlyWeightMeta:
            metaclass for flyweight objects  

        ------------------------------------

        class A:
            def __init__(self, a):
                self.a = a

        class B(metaclass=FlyWeightMeta):
            def __init__(self, a):
                self.a = a

        class C(metaclass=FlyWeightMeta):
            def __init__(self, a):
                self.a = a

        assert A(0) is not A(0)
        assert B(0) is B(0)
        assert B(0) is not B(1)
        assert B(0) is not C(0)
    '''

    def __call__(cls, *pargs, **kwargs):
        idx = (pargs, tuple(kwargs.items()))
        try:
            return cls.__instances[idx]
        except KeyError:
            obj = cls.__new__(cls, *pargs, **kwargs)
            cls.__init__(obj, *pargs, **kwargs)
            cls.__instances[idx] = obj
            return obj


    def __init__(cls, *pargs, **kwargs):
        super().__init__(cls, pargs, kwargs)
        cls.__instances = dict()



class ValidContainer:
    '''wrapper class that allows data to marked invalid '''
    __slots__ = '_data', '_valid'

    def __init__(self):
        self.mark_invalid()

    def mark_invalid(self):
        self._valid = False

    @property
    def valid(self):
        return self._valid

    @property
    def data(self):
        if not self.valid:
            raise AttributeError('Data is invalid')
        return self._data

    @data.setter
    def data(self, data):
        self._valid = True
        self._data = data

    def __repr__(self):
        if self.valid:
            return repr(data)
        else:
            return 'Invalid'

