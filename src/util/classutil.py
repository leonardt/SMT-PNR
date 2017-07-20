import itertools as it
import collections
import weakref

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
        import gc

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
        gc.disable()
        ID = id(B(0))
        assert ID == id(B(0))
        gc.enable()
        gc.collect()
        assert ID != id(B(0))
    '''

    __instances = weakref.WeakValueDictionary()
    def __call__(objtype, *pargs, **kwargs):
        idx = (objtype, pargs, tuple(kwargs.items()))
        try:
            return FlyWeightMeta.__instances[idx]
        except KeyError:
            obj = super().__call__(*pargs, **kwargs)
            FlyWeightMeta.__instances[idx] = obj
            return obj


    def __new__(cls, name, bases, attrs, **kwargs):
        if '__slots__' in attrs and '__dict__' not in attrs and '__weakref__' not in attrs['__slots__']:
            #make sure its not in a base
            for b in bases:
                if hasattr(b, '__slots__') and '__weakref__' in b.__slots__:
                    break
            else:
                #made through the loop not in a base
                attrs['__slots__'] = attrs['__slots__'] + ('__weakref__',)
        return super().__new__(cls, name, bases, attrs, **kwargs)

class Constant(metaclass=FlyWeightMeta):
    __slots__ = ()

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


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

class class_property:
    __slots__ = 'fget', '__doc__'
    def __init__(self, fget, doc=None):
        ''' Descriptor for read only class properties '''
        if doc is None and fget.__doc__ is not None:
            doc = fget.__doc__

        self.fget = fget
        self.__doc__ = doc

    def __get__(self, obj, objtype=None):
        return self.fget(objtype)

    def __set__(self, obj, objtype=None):
        raise AttributeError('Class Properties must be read only')


def namedtuple_with_defaults(typename, field_names, default_values=()):
    '''
       Creates namedtuple with default values
       From:
           https://stackoverflow.com/questions/11351032/named-tuple-and-optional-keyword-arguments
    '''
    T = collections.namedtuple(typename, field_names)
    T.__new__.__defaults__ = (None,) * len(T._fields)
    if isinstance(default_values, collections.Mapping):
        prototype = T(**default_values)
    else:
        prototype = T(*default_values)
    T.__new__.__defaults__ = tuple(prototype)
    return T


def namedtuple_init_eq(typename, field_names):

    '''
       Creates a namedtuple. If passed only one value, then it assigns
       all field names to that value
    '''

    def _construct_namedtuple(value):
        T = collections.namedtuple(typename, field_names)
        names = field_names.replace(',', ' ').split()
        if isinstance(value, collections.Sequence):
            assert len(names) == len(value), \
                'mismatch in length of field names and passed parameters'

            d = dict()
            for k, v in zip(names, value):
                d[k] = v
        else:
            d = dict.fromkeys(names, value)

        return T(**d)

    return _construct_namedtuple
