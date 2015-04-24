import itertools
from operator import attrgetter

try:
    import simplejson as json
except:
    import json

from django.contrib.auth.models import User

from xblock.fields import Scope
from xblock_user_state.interface import XBlockUserStateClient
from courseware.models import StudentModule

class DjangoXBlockUserStateClient(XBlockUserStateClient):
    """
    An interface that uses the Django ORM StudentModule as a backend.
    """

    class ServiceUnavailableError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    def __init__(self, user):
        self._student_module_cache = {}
        self.user = user

    def get(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key (UsageKey): The UsageKey identifying which xblock state to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Returns
            dict: A dictionary mapping field names to values
        """
        assert self.user.username == username
        return next(self.get_many(username, [block_key], scope, fields=fields))[1]

    def set(self, username, block_key, state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key (UsageKey): The UsageKey identifying which xblock state to update.
            state (dict): A dictionary mapping field names to values
            scope (Scope): The scope to load data from
        """
        assert self.user.username == username
        self.set_many(username, {block_key: state}, scope)

    def delete(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        assert self.user.username == username
        return self.delete_many(username, [block_key], scope, fields=fields)

    def _get_field_objects(self, username, block_keys):
        course_key_func = attrgetter('course_key')
        by_course = itertools.groupby(
            sorted(block_keys, key=course_key_func),
            course_key_func,
        )

        for course_key, usage_keys in by_course:
            not_cached = []
            for usage_key in usage_keys:
                if usage_key in self._student_module_cache:
                    yield self._student_module_cache[usage_key]
                else:
                    not_cached.append(usage_key)

            query = StudentModule.objects.chunked_filter(
                'module_state_key__in',
                not_cached,
                student__username=username,
                course_id=course_key,
            )

            for student_module in query:
                usage_key = student_module.module_state_key.map_into_course(student_module.course_id)
                self._student_module_cache[usage_key] = student_module
                yield student_module


    def get_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys ([UsageKey]): A list of UsageKeys identifying which xblock states to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Yields:
            (UsageKey, field_state) tuples for each specified UsageKey in block_keys.
            field_state is a dict mapping field names to values.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported, not {}".format(scope))

        query = self._get_field_objects(username, block_keys)
        for module in query:
            usage_key = module.module_state_key.map_into_course(module.course_id)
            if module.state is None:
                state = {}
            else:
                state = json.loads(module.state)
            yield (usage_key, state)

    def set_many(self, username, block_keys_to_state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys_to_state (dict): A dict mapping UsageKeys to state dicts.
                Each state dict maps field names to values. These state dicts
                are overlaid over the stored state. To delete fields, use
                :meth:`delete` or :meth:`delete_many`.
            scope (Scope): The scope to load data from
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        field_objects = self._get_field_objects(username, block_keys_to_state.keys())
        for field_object in field_objects:
            usage_key = field_object.module_state_key.map_into_course(field_object.course_id)
            current_state = json.loads(field_object.state)
            current_state.update(block_keys_to_state.pop(usage_key))
            field_object.state = json.dumps(current_state)
            field_object.save()

        for usage_key, state in block_keys_to_state.items():
            student_module, created = StudentModule.objects.get_or_create(
                student=self.user,
                course_id=usage_key.course_key,
                module_state_key=usage_key,
                defaults={
                    'state': json.dumps(state),
                    'module_type': usage_key.block_type,
                },
            )

            if not created:
                if student_module.state is None:
                    current_state = {}
                else:
                    current_state = json.loads(student_module.state)
                current_state.update(state)
                student_module.state = json.dumps(current_state)
                # We just read this object, so we know that we can do an update
                student_module.save(force_update=True)

    def delete_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a many xblock usages.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        field_objects = self._get_field_objects(username, block_keys)
        for field_object in field_objects:
            usage_key = field_object.module_state_key.map_into_course(field_object.course_id)
            if fields is None:
                field_object.state = "{}"
            else:
                current_state = json.loads(field_object.state)
                for field in fields:
                    if field in current_state:
                        del current_state[field]

                field_object.state = json.dumps(current_state)
            field_object.save()

    def get_history(self, username, block_key, scope=Scope.user_state):
        """We don't guarantee that history for many blocks will be fast."""
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()

    def iter_all_for_block(self, block_key, scope=Scope.user_state, batch_size=None):
        """
        You get no ordering guarantees. Fetching will happen in batch_size
        increments. If you're using this method, you should be running in an
        async task.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()

    def iter_all_for_course(self, course_key, block_type=None, scope=Scope.user_state, batch_size=None):
        """
        You get no ordering guarantees. Fetching will happen in batch_size
        increments. If you're using this method, you should be running in an
        async task.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()

