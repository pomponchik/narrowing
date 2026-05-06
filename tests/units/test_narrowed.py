import re
from typing import Any, List, Optional

import pytest

from narrowing import Narrowed


def test_isinstance_passes_through_predicate(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert isinstance(5, narrowed_class)
    assert isinstance(1, narrowed_class)
    assert not isinstance(0, narrowed_class)
    assert not isinstance(-1, narrowed_class)


def test_isinstance_rejects_wrong_base_type(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert not isinstance('123', narrowed_class)
    assert not isinstance('kek', narrowed_class)


def test_issubclass_with_underlying_type(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert issubclass(narrowed_class, int)


def test_issubclass_with_unrelated_type(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert not issubclass(narrowed_class, str)


def test_issubclass_underlying_type_is_not_a_subclass(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert not issubclass(int, narrowed_class)


def test_issubclass_object_is_always_a_supertype(make_narrowed):
    narrowed_class = make_narrowed(int, 'x > 0')

    assert issubclass(narrowed_class, object)


def test_two_independent_subscriptions_are_distinct(make_narrowed):
    first = make_narrowed(int, 'x > 0')
    second = make_narrowed(int, 'x > 0')

    assert not issubclass(first, second)
    assert not issubclass(second, first)


def test_subscription_is_reflexive(make_narrowed):
    narrowed = make_narrowed(int, 'x > 0')

    assert issubclass(narrowed, narrowed)


def test_subscript_with_lambda_is_rejected():
    """Cross-form: lambda inside subscript is forbidden, points at call form."""
    with pytest.raises(TypeError, match='lambda predicate is only valid in call form'):
        Narrowed[int, lambda x: x > 0]


def test_call_with_string_is_rejected():
    """Cross-form: string inside call is forbidden, points at subscript form."""
    with pytest.raises(TypeError, match='string predicate is only valid in subscript form'):
        Narrowed(int, 'x > 0')


def test_call_with_non_class_base():
    with pytest.raises(TypeError, match='first argument must be a class'):
        Narrowed(42, lambda x: x > 0)


def test_subscript_with_non_class_base():
    with pytest.raises(TypeError, match='first argument must be a class'):
        Narrowed[42, 'x > 0']


def test_call_with_kwargs_is_rejected():
    with pytest.raises(TypeError, match='does not accept keyword arguments'):
        Narrowed(int, predicate=lambda x: x > 0)


def test_subscript_with_int_predicate_is_rejected():
    with pytest.raises(TypeError, match='predicate must be a string'):
        Narrowed[int, 42]


def test_call_arity_too_few():
    with pytest.raises(TypeError, match=r'expected Narrowed\(T, predicate\), got 1 positional argument'):
        Narrowed(int)


def test_call_arity_too_many():
    with pytest.raises(TypeError, match=r'expected Narrowed\(T, predicate\), got 3 positional argument'):
        Narrowed(int, lambda x: x > 0, 'extra')


def test_subscript_arity_too_few():
    with pytest.raises(TypeError, match=r'expected Narrowed\[T, "expr"\], got 1 parameter'):
        Narrowed[int]


def test_subscript_arity_too_many():
    with pytest.raises(TypeError, match=r'expected Narrowed\[T, "expr"\], got 3 parameter'):
        Narrowed[int, 'x > 0', 'extra']


def test_constructor_passes_through_unchanged(make_narrowed):
    """The alias acts as a validating identity function for valid values."""
    PositiveInt = make_narrowed(int, 'x > 0')
    result = PositiveInt(5)

    assert result == 5
    assert type(result) is int


def test_constructor_rejects_value_failing_predicate(make_narrowed):
    PositiveInt = make_narrowed(int, 'x > 0')

    with pytest.raises(TypeError, match=r'predicate rejected value -5'):
        PositiveInt(-5)


def test_constructor_rejects_value_with_wrong_type(make_narrowed):
    PositiveInt = make_narrowed(int, 'x > 0')

    with pytest.raises(TypeError, match=r"expected int, got <class 'str'>"):
        PositiveInt('5')


def test_constructor_without_arguments_raises(make_narrowed):
    PositiveInt = make_narrowed(int, 'x > 0')

    with pytest.raises(TypeError):
        PositiveInt()


def test_nested_narrowing_passes_when_both_predicates_satisfied(make_narrowed):
    """Composition of two Narrowed types validates against both predicates."""
    Bounded = make_narrowed(make_narrowed(int, 'x > 0'), 'x < 100')

    assert isinstance(50, Bounded)


def test_nested_narrowing_rejects_inner_predicate_failure(make_narrowed):
    Bounded = make_narrowed(make_narrowed(int, 'x > 0'), 'x < 100')

    assert not isinstance(-5, Bounded)


def test_nested_narrowing_rejects_outer_predicate_failure(make_narrowed):
    Bounded = make_narrowed(make_narrowed(int, 'x > 0'), 'x < 100')

    assert not isinstance(150, Bounded)


def test_nested_subscript_form_inside_call_form():
    """Mixed-form composition: subscript-form alias used as base for a call-form alias."""
    Bounded = Narrowed(Narrowed[int, 'x > 0'], lambda x: x < 100)

    assert isinstance(50, Bounded)
    assert not isinstance(-5, Bounded)
    assert not isinstance(150, Bounded)


def test_nested_call_form_inside_subscript_form():
    """Mixed-form composition: call-form alias used as base for a subscript-form alias."""
    Bounded = Narrowed[Narrowed(int, lambda x: x > 0), 'x < 100']

    assert isinstance(50, Bounded)
    assert not isinstance(-5, Bounded)
    assert not isinstance(150, Bounded)


def test_lambda_with_two_args_rejected():
    with pytest.raises(TypeError, match='exactly one positional argument'):
        Narrowed(int, lambda x, y: x > y)


def test_lambda_with_no_args_rejected():
    with pytest.raises(TypeError, match='exactly one positional argument'):
        Narrowed(int, lambda: True)


def test_lambda_with_default_value_rejected():
    with pytest.raises(TypeError, match='must not have default values'):
        Narrowed(int, lambda x=5: x > 0)


def test_lambda_with_varargs_rejected():
    with pytest.raises(TypeError, match='exactly one positional argument'):
        Narrowed(int, lambda *args: True)


def test_lambda_with_argcount_one_plus_varargs_rejected():
    """A lambda with `(x, *args)` has argcount=1 but still has varargs."""
    namespace: dict = {}
    exec('function = lambda x, *args: x > 0', namespace)

    with pytest.raises(TypeError, match='exactly one positional argument'):
        Narrowed(int, namespace['function'])


def test_lambda_with_kwonly_args_rejected():
    namespace: dict = {}
    exec('function = lambda *, x: x > 0', namespace)

    with pytest.raises(TypeError, match='exactly one positional argument'):
        Narrowed(int, namespace['function'])


def test_lambda_with_closure_capture_rejected():
    """A closure over a free variable is forbidden — predicates must be self-contained."""
    def outer():
        captured_value = 5
        return lambda x: x > captured_value

    with pytest.raises(TypeError, match='must not capture closure'):
        Narrowed(int, outer())


def test_lambda_referencing_undefined_global_rejected():
    namespace: dict = {}
    exec('function = lambda x: x > some_global', namespace)

    with pytest.raises(TypeError, match=r"references free name 'some_global'"):
        Narrowed(int, namespace['function'])


def test_lambda_referencing_non_whitelist_builtin_rejected():
    namespace: dict = {}
    exec('function = lambda x: __import__("os")', namespace)

    with pytest.raises(TypeError, match='references free name'):
        Narrowed(int, namespace['function'])


def test_def_function_rejected():
    """Only lambdas are accepted in call-form; def-style functions are not."""
    def positive(x):
        return x > 0

    with pytest.raises(TypeError, match='must be a lambda'):
        Narrowed(int, positive)


def test_int_in_predicate_position_rejected():
    with pytest.raises(TypeError, match='must be a lambda'):
        Narrowed(int, 42)


def test_attribute_access_on_argument_is_allowed(make_narrowed):
    """`x.y` is valid: the `x` is the argument; `y` is an attribute access, not a free name."""
    class Box:
        y = 0

    Positive = make_narrowed(Box, 'x.y > 0')
    instance = Box()

    instance.y = 5
    assert isinstance(instance, Positive)

    instance.y = 0
    assert not isinstance(instance, Positive)


def test_attribute_access_still_validates_base_type(make_narrowed):
    class Box:
        y = 0

    Positive = make_narrowed(Box, 'x.y > 0')

    assert not isinstance(5, Positive)
    assert not isinstance('not a Box', Positive)


def test_walrus_operator_in_predicate(make_narrowed):
    """The walrus target is treated as a local in the predicate's scope."""
    Positive = make_narrowed(int, '(y := x + 1) > 0')

    assert isinstance(0, Positive)
    assert not isinstance(-2, Positive)


def test_whitelist_builtin_in_predicate(make_narrowed):
    """`len` is in the builtin allowlist and may be referenced freely."""
    NonEmpty = make_narrowed(list, 'len(x) > 0')

    assert isinstance([1], NonEmpty)
    assert not isinstance([], NonEmpty)
    assert not isinstance(5, NonEmpty)


def test_invalid_string_predicate_syntax():
    with pytest.raises(TypeError, match='invalid predicate expression'):
        Narrowed[int, 'x >']


def test_empty_string_predicate():
    with pytest.raises(TypeError, match='must not be empty'):
        Narrowed[int, '']


def test_string_predicate_with_unknown_argument_name():
    """Only `x` is the implicit argument inside a string predicate."""
    with pytest.raises(TypeError, match=r"references free name 'value'"):
        Narrowed[int, 'value > 0']


def test_string_predicate_with_nested_lambda():
    """Nested lambdas inside string predicates extend the local scope by their args."""
    narrowed_class = Narrowed[list, '(lambda value: value > 0)(x[0])']

    assert isinstance([5], narrowed_class)
    assert not isinstance([0], narrowed_class)


def test_string_predicate_with_nested_lambda_varargs_and_kwargs():
    narrowed_class = Narrowed[int, '(lambda *args, **kwargs: args[0] if args else 0)(x) > 0']

    assert isinstance(5, narrowed_class)
    assert not isinstance(-5, narrowed_class)


def test_string_predicate_with_listcomp():
    NonNegative = Narrowed[list, 'all(item > 0 for item in x)']

    assert isinstance([1, 2, 3], NonNegative)
    assert not isinstance([1, -2, 3], NonNegative)


def test_string_predicate_with_dictcomp():
    narrowed_class = Narrowed[list, 'len({key: value for key, value in enumerate(x)}) > 0']

    assert isinstance([1, 2], narrowed_class)
    assert not isinstance([], narrowed_class)


def test_string_predicate_with_setcomp():
    narrowed_class = Narrowed[list, 'len({item for item in x}) == len(x)']

    assert isinstance([1, 2, 3], narrowed_class)
    assert not isinstance([1, 1, 2], narrowed_class)


def test_string_predicate_with_listcomp_tuple_target():
    """Tuple targets in comprehensions decompose into individual local names."""
    narrowed_class = Narrowed[dict, 'all(value > 0 for key, value in x.items())']

    assert isinstance({'a': 1, 'b': 2}, narrowed_class)
    assert not isinstance({'a': 1, 'b': -1}, narrowed_class)


def test_string_predicate_with_listcomp_filter():
    narrowed_class = Narrowed[list, '[item for item in x if item > 0]']

    assert isinstance([1, 2, -1], narrowed_class)


def test_t_can_be_none_type():
    NoneOnly = Narrowed(type(None), lambda x: x is None)

    assert isinstance(None, NoneOnly)
    assert not isinstance(0, NoneOnly)


def test_t_can_be_parameterized_generic():
    """List[int] base also gets simtypes content validation in strict mode."""
    NonEmptyIntList = Narrowed(List[int], lambda value: len(value) > 0)

    assert isinstance([1, 2, 3], NonEmptyIntList)
    assert not isinstance([], NonEmptyIntList)
    assert not isinstance(['a', 'b'], NonEmptyIntList)


def test_t_can_be_optional():
    NoneOrPositive = Narrowed(Optional[int], lambda x: x is None or x > 0)

    assert isinstance(None, NoneOrPositive)
    assert isinstance(5, NoneOrPositive)
    assert not isinstance(-5, NoneOrPositive)


def test_t_can_be_any():
    AnyTrue = Narrowed(Any, lambda x: True)

    assert isinstance(5, AnyTrue)
    assert isinstance('string', AnyTrue)


def test_narrowed_class_cannot_be_subclassed():
    with pytest.raises(TypeError, match='cannot be subclassed'):
        class Foo(Narrowed):
            pass


def test_repr_contains_base_type_for_call_form():
    narrowed_class = Narrowed(int, lambda x: x > 0)
    text = repr(narrowed_class)

    assert 'Narrowed' in text
    assert 'int' in text


def test_repr_contains_predicate_string_for_subscript_form():
    narrowed_class = Narrowed[int, 'x > 0']
    text = repr(narrowed_class)

    assert 'Narrowed' in text
    assert 'int' in text
    assert 'x > 0' in text


def test_repr_for_none_base():
    narrowed_class = Narrowed(None, lambda x: x is None)
    text = repr(narrowed_class)

    assert 'None' in text


def test_repr_for_any_base():
    narrowed_class = Narrowed(Any, lambda x: True)
    text = repr(narrowed_class)

    assert 'Any' in text


def test_repr_for_parameterized_generic_base():
    narrowed_class = Narrowed(List[int], lambda value: True)
    text = repr(narrowed_class)

    assert 'list' in text.lower() or 'List' in text


def test_repr_for_uniontype_base():
    """`int | str` has no `__qualname__`; the repr falls back to `repr(base)`."""
    narrowed_class = Narrowed(int | str, lambda x: True)

    assert isinstance(5, narrowed_class)
    assert isinstance('string', narrowed_class)


def test_repr_falls_back_when_lambda_source_unavailable():
    """When source can't be extracted (e.g. exec'd lambda), repr uses '<lambda>' literal."""
    namespace: dict = {}
    exec('function = lambda x: x > 0', namespace)
    narrowed_class = Narrowed(int, namespace['function'])
    text = repr(narrowed_class)

    assert 'lambda' in text.lower()


def test_subclasscheck_falls_through_to_type_default():
    """Non-reflexive issubclass goes through `type.__subclasscheck__`."""
    narrowed_class = Narrowed(int, lambda x: x > 0)

    class MyInt(int):
        pass

    assert not issubclass(MyInt, narrowed_class)


def test_lambda_using_imported_stdlib_module():
    NonEmpty = Narrowed(str, lambda string_value: re.match(r'.', string_value) is not None)

    assert isinstance('a', NonEmpty)
    assert not isinstance('', NonEmpty)
    assert not isinstance(5, NonEmpty)


def test_string_predicate_using_imported_stdlib_module():
    Email = Narrowed[str, "re.match(r'.', x) is not None"]

    assert isinstance('a', Email)
    assert not isinstance('', Email)


def test_subclassable_cache_does_not_break_repeated_call_with_same_base():
    first = Narrowed(int, lambda x: x > 0)
    second = Narrowed(int, lambda x: x > 5)

    assert issubclass(first, int)
    assert issubclass(second, int)


def test_non_subclassable_base_uses_metaclass_fallback():
    """`bool` is not subclassable in CPython; the alias falls back to bases=()."""
    narrowed_class = Narrowed(bool, lambda x: x is True)

    assert isinstance(True, narrowed_class)
    assert not isinstance(False, narrowed_class)
    assert bool not in narrowed_class.__mro__


def test_subclassable_cache_survives_garbage_collected_class_with_same_qualname():
    """
    The cache keys on module + qualname rather than `id(...)`. If we cached
    by id, allocating a fresh class with the same name in the same address
    range as a collected one would produce a stale True/False reading.
    """
    import gc

    def make_class():
        class Local:
            pass
        return Local

    first = Narrowed(make_class(), lambda x: True)
    assert isinstance(first, type)

    gc.collect()

    second_base = make_class()
    second = Narrowed(second_base, lambda x: True)
    assert isinstance(second, type)
    assert second_base in second.__mro__
