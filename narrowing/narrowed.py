"""
Runtime implementation of `Narrowed`.

Two surface-syntax forms are supported and produce identical classes:

    PositiveInt = Narrowed(int, lambda x: x > 0)     # call form
    PositiveInt = Narrowed[int, 'x > 0']             # subscript-with-string form

Both go through the shared factory `_build_narrowed_class` and are rejected
for the wrong predicate type (lambda inside subscript, string inside call)
with a message pointing at the right form.
"""
import inspect
import sys
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Mapping,
    NoReturn,
    Tuple,
    Type,
    TypeVar,
    cast,
    get_origin,
    overload,
)

from getsources import getclearsource
from printo import superrepr
from simtypes import check

from narrowing.lambda_check import make_string_predicate, validate_lambda


def _is_subclassable(type_object: type) -> bool:
    """
    Return True if `type_object` can serve as a base for a dynamically created
    subclass. Some CPython built-ins (`bool`, `range`, `slice`, ...) don't have
    `Py_TPFLAGS_BASETYPE` set; subclassing them raises `TypeError`.
    """
    try:
        # `type(name, bases, dict)` returns `type` per stub; we only care if
        # the call raises, so the typed-Any return value is fine to discard.
        type('_probe', (type_object,), {})  # type: ignore[misc]
    except TypeError:
        return False
    return True


def _format_base(base: object) -> str:
    if base is None:
        return 'None'
    if base is Any:
        return 'Any'
    qualname: object = getattr(base, '__qualname__', None)
    if isinstance(qualname, str):
        return qualname
    return repr(base)


def _validate_base(target_type: object) -> None:
    if target_type is None:
        return
    if target_type is Any:
        return
    origin: object = get_origin(target_type)
    if origin is not None:
        return
    if inspect.isclass(target_type):
        return
    raise TypeError(f'narrowing: first argument must be a class, got {target_type!r}')


class NarrowedMeta(type):
    """Metaclass for narrowed classes; wires runtime predicate and type checks."""

    _narrowing_base: object
    _narrowing_pred: Callable[[object], object]
    _narrowing_repr_source: str

    def __instancecheck__(cls, instance: object) -> bool:
        # `simtypes.check` returns TypeIs[T] which mypy unwraps to bool.
        # `cls._narrowing_base` is typed `object`; simtypes accepts any type.
        if not check(instance, cls._narrowing_base, strict=True):  # type: ignore[arg-type, misc, unused-ignore]
            return False
        return bool(cls._narrowing_pred(instance))

    def __subclasscheck__(cls, subclass: type) -> bool:
        if subclass is cls:
            return True
        # `type.__subclasscheck__` is typed via `type[type]` Any in typeshed.
        return type.__subclasscheck__(cls, subclass)  # type: ignore[misc]

    def __repr__(cls) -> str:
        return f'Narrowed[{superrepr(cls._narrowing_base)}, {superrepr(cls._narrowing_repr_source)}]'


def _build_narrowed_class(
    base: object,
    predicate: Callable[[object], object],
    repr_source: str,
) -> Type[object]:
    def narrowed_new(_inner_class: type, value: object) -> object:
        if not check(value, base, strict=True):  # type: ignore[arg-type, misc, unused-ignore]
            raise TypeError(
                f'narrowing: expected {_format_base(base)}, got {type(value)!r}',
            )
        if not bool(predicate(value)):
            raise TypeError(f'narrowing: predicate rejected value {value!r}')
        return value

    namespace: Dict[str, object] = {
        '_narrowing_base': base,
        '_narrowing_pred': staticmethod(predicate),
        '_narrowing_repr_source': repr_source,
        '__new__': staticmethod(narrowed_new),
    }
    # `base` is `object` at the type level, but `inspect.isclass` narrows it to
    # `type` enough for the runtime call; mypy can't follow that into a tuple.
    bases: Tuple[type, ...] = (
        (base,) if (inspect.isclass(base) and _is_subclassable(base)) else ()  # type: ignore[misc]
    )
    name = f'Narrowed[{_format_base(base)}, {repr_source!r}]'
    return NarrowedMeta(name, bases, namespace)


def _normalize_call_args(
    args: Tuple[object, ...],
    kwargs: Dict[str, object],
) -> Tuple[object, Callable[[object], object], str]:
    if kwargs:
        raise TypeError('narrowing: Narrowed does not accept keyword arguments')
    if len(args) != 2:
        raise TypeError(
            f'narrowing: expected Narrowed(T, predicate), got {len(args)} positional argument(s)',
        )
    base, predicate = args
    if isinstance(predicate, str):
        raise TypeError(
            'narrowing: string predicate is only valid in subscript form Narrowed[T, "expr"]; '
            'for call form, pass a lambda: Narrowed(T, lambda x: ...)',
        )
    _validate_base(base)
    validate_lambda(predicate)
    # `validate_lambda` rejects non-LambdaType inputs; past that point `predicate`
    # is statically a lambda. mypy can't follow that through `object`, so we
    # cast back to the callable shape we hand off downstream.
    predicate_callable = cast(Callable[[object], object], predicate)
    return base, predicate_callable, _lambda_repr_source(predicate_callable)


def _lambda_repr_source(function: Callable[[object], object]) -> str:
    try:
        return getclearsource(function)
    except Exception:  # noqa: BLE001
        return '<lambda>'


def _normalize_subscript_params(
    params: object,
    caller_globals: Mapping[str, object],
) -> Tuple[object, Callable[[object], object], str]:
    if not isinstance(params, tuple):
        raise TypeError(
            'narrowing: expected Narrowed[T, "expr"], got 1 parameter(s)',
        )
    if len(params) != 2:
        raise TypeError(
            f'narrowing: expected Narrowed[T, "expr"], got {len(params)} parameter(s)',
        )
    base, expression = params
    if not isinstance(expression, str):
        if callable(expression) and not isinstance(expression, type):  # type: ignore[misc]
            raise TypeError(
                'narrowing: lambda predicate is only valid in call form Narrowed(T, lambda x: ...); '
                'for subscript form, pass a string: Narrowed[T, "x > 0"]',
            )
        raise TypeError(
            f'narrowing: predicate must be a string, got {type(expression).__name__}',
        )
    _validate_base(base)
    predicate_callable = make_string_predicate(expression, caller_globals)
    return base, predicate_callable, expression


BaseType = TypeVar('BaseType')


class Narrowed(Generic[BaseType]):
    """
    Predicate-based type narrowing. Use one of the two equivalent forms:

        PositiveInt = Narrowed(int, lambda x: x > 0)
        PositiveInt = Narrowed[int, 'x > 0']
    """

    @overload
    def __new__(  # type: ignore[misc]
        cls,
        base: Type[BaseType],
        predicate: Callable[[BaseType], object],
    ) -> Type[BaseType]: ...
    @overload
    def __new__(cls, *args: Any, **kwargs: Any) -> Type[Any]: ...  # type: ignore[misc]

    def __new__(cls, *args: Any, **kwargs: Any) -> Type[Any]:  # type: ignore[misc]
        base, predicate, repr_source = _normalize_call_args(args, kwargs)  # type: ignore[misc]
        return _build_narrowed_class(base, predicate, repr_source)

    def __class_getitem__(cls, params: object) -> Type[object]:
        caller_globals: Mapping[str, object] = sys._getframe(1).f_globals
        base, predicate, repr_source = _normalize_subscript_params(params, caller_globals)
        return _build_narrowed_class(base, predicate, repr_source)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError(
            'narrowing: Narrowed cannot be subclassed; '
            'use Narrowed(T, lambda) or Narrowed[T, "expr"]',
        )
