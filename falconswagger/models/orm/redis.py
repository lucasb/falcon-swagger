# MIT License

# Copyright (c) 2016 Diogo Dutra

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


from falconswagger.models.orm.redis_base import ModelRedisBaseMeta, ModelRedisBase
from collections import OrderedDict
from copy import deepcopy
from types import MethodType
import msgpack


class ModelRedisMeta(ModelRedisBaseMeta):
    CHUNKS = 100

    def insert(cls, session, objs, **kwargs):
        input_ = deepcopy(objs)
        objs = cls._to_list(objs)
        ids_objs_map = dict()
        counter = 0

        for obj in objs:
            obj = cls(obj)
            obj_key = obj.get_key()
            ids_objs_map[obj_key] = msgpack.dumps(obj)
            counter += 1

            if counter == cls.CHUNKS:
                session.redis_bind.hmset(cls.__key__, ids_objs_map)
                ids_objs_map = dict()
                counter = 0

        if ids_objs_map:
            session.redis_bind.hmset(cls.__key__, ids_objs_map)

        return objs

    def update(cls, session, objs, ids=None, **kwargs):
        input_ = deepcopy(objs)

        objs = cls._to_list(objs)
        if ids:
            keys_objs_map = cls._build_keys_objs_map_with_ids(objs, ids)
        else:
            keys_objs_map = OrderedDict([(cls.get_instance_key(obj), obj) for obj in objs])

        keys = set(keys_objs_map.keys())
        keys.difference_update(set(session.redis_bind.hkeys(cls.__key__)))
        keys.intersection_update(keys)
        invalid_keys = keys

        for key in invalid_keys:
            keys_objs_map.pop(key, None)

        keys_objs_to_del = dict()

        if keys_objs_map:
            set_map = OrderedDict()
            counter = 0
            for key in set(keys_objs_map.keys()):
                obj = keys_objs_map[key]
                if obj.get('_operation') == 'delete':
                    keys_objs_to_del[key] = obj
                    keys_objs_map.pop(key)
                    continue

                set_map[key] = msgpack.dumps(obj)
                counter += 1

                if counter == cls.CHUNKS:
                    session.redis_bind.hmset(cls.__key__, set_map)
                    set_map = OrderedDict()
                    counter = 0

            if set_map:
                session.redis_bind.hmset(cls.__key__, set_map)

        if keys_objs_to_del:
            session.redis_bind.hdel(cls.__key__, *keys_objs_to_del.keys())

        return list(keys_objs_map.values()) or list(keys_objs_to_del.values())

    def _build_keys_objs_map_with_ids(cls, objs, ids):
        ids = cls._to_list(ids)
        keys_objs_map = OrderedDict()

        for obj in objs:
            obj_ids = cls(obj).get_ids_map(ids[0].keys())
            if obj_ids in ids:
                keys_objs_map[cls._build_key(obj_ids)] = obj

        return keys_objs_map

    def _build_key(cls, id_):
        return cls.get_instance_key(id_, id_.keys())

    def delete(cls, session, ids, **kwargs):
        keys = [cls._build_key(id_) for id_ in cls._to_list(ids)]
        if keys:
            session.redis_bind.hdel(cls.__key__, *keys)

    def get(cls, session, ids=None, limit=None, offset=None, **kwargs):
        if limit is not None and offset is not None:
            limit += offset

        elif ids is None and limit is None and offset is None:
            return cls._unpack_objs(session.redis_bind.hgetall(cls.__key__))

        if ids is None:
            keys = [k for k in session.redis_bind.hkeys(cls.__key__)][offset:limit]
            if keys:
                return cls._unpack_objs(session.redis_bind.hmget(cls.__key__, *keys))
            else:
                return []
        else:
            ids = [cls._build_key(id_) for id_ in cls._to_list(ids)]
            return cls._unpack_objs(session.redis_bind.hmget(cls.__key__, *ids[offset:limit]))

    def _unpack_objs(cls, objs):
        if isinstance(objs, dict):
            objs = objs.values()
        return [msgpack.loads(obj, encoding='utf-8') for obj in objs if obj is not None]


class _ModelRedis(dict, ModelRedisBase):

    @classmethod
    def set_ids(cls, instance, key, keys=None):
        if keys is None:
            keys = sorted(cls.__id_names__)

        values = key.split(cls.__keys_separator__)
        for key, value in zip(keys, values):
            instance[key] = value.decode()

    def get_ids_map(self, keys=None):
        if keys is None:
            keys = type(self).__id_names__

        keys = sorted(keys)
        return {key: self[key] for key in keys}


class ModelRedisFactory(object):

    @staticmethod
    def make(class_name, key, id_names, schema=None, metaclass=None, keys_separator=b'|'):
        if metaclass is None:
            metaclass = ModelRedisMeta

        attributes = {
            '__key__': key,
            '__id_names__': sorted(tuple(id_names)),
            '__keys_separator__': \
                keys_separator.decode() if isinstance(keys_separator, str) else keys_separator
        }
        if schema is not None:
            attributes['__schema__'] = schema

        model = metaclass(class_name, (_ModelRedis,), attributes)
        model.update = MethodType(metaclass.update, model)
        model.update_ = MethodType(dict.update, model)
        model.get = MethodType(metaclass.get, model)
        return model
