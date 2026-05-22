# Narrowing — Implementation Plan (v4)

## Context

`narrowing` — Python-библиотека, дающая тип `Narrowed` с одновременно рантайм- и статик-валидацией через mypy-плагин.

**Ключевое API-решение (v4)**: поддерживаем **две эквивалентные формы** записи. Обе создают идентичный по поведению рантайм-класс через общую фабрику.

```python
# Форма B (call-syntax) — primary
PositiveInt = Narrowed(int, lambda x: x > 0)

# Форма E (subscript-with-string) — secondary, gated by mypy spike
PositiveInt = Narrowed[int, 'x > 0']
```

Внутри строки в форме E `x` — фиксированный sentinel, единственное допустимое «свободное» имя; играет роль аргумента лямбды. Зеркальность с B-формой (`lambda x: x > 0` ↔ `'x > 0'`) — намеренная: одно ментальное имя для одной роли, лёгкий visual-switch между формами.

**Почему две формы**:
- B работает на компилированном mypy штатно через `get_dynamic_class_hook`. Это hard-requirement; без него проект не имеет смысла.
- E *вероятно* работает на компилированном mypy: строка в типовой позиции — это forward reference, парсинг которой mypy откладывает на стадию type analysis. Наш `get_type_analyze_hook` перехватывает родительский `Narrowed` до того, как mypy попытается распарсить строку. В отличие от `LambdaExpr`, `StrExpr` штатно проходит C-translator. **Подтверждение требует спайка** (см. step 1).
- Если спайк E не пройдёт — оставляем B как единственный alias-form, E работает только в inline-позициях (`isinstance(x, Narrowed[int, 'x > 0'])`, где type-translation не применяется).

**Breaking change vs. существующий код**: оба теста пользователя в `tests/units/test_narrowed.py` используют subscript-форму с лямбдой (`Narrowed[int, lambda x: x > 0]`). Эта форма **отвергается** обеими нашими реализациями (B требует call, E требует строку, не лямбду). Тесты переписываются на одну из двух поддерживаемых форм.

Скелет репозитория: `narrowing/narrowed.py` (пустой), `narrowing/plugin.py` (пустой), `narrowing/__init__.py` (`from narrowing.narrowed import Narrowed`), `tests/units/test_narrowed.py` (2 теста — переписать), `tests/typing/test_narrowed.py` (пустой), `tests/documentation/test_readme.py` (пустой). Всё остальное — TDD.

## Surface syntax

### Поддерживаемые формы

```python
# B: call с лямбдой
Narrowed(int, lambda x: x > 0)
Narrowed(str, lambda s: '@' in s)

# E: subscript со строкой
Narrowed[int, 'x > 0']
Narrowed[str, '"@" in x']
```

### Запрещённые формы

```python
Narrowed[int, lambda x: x > 0]    # subscript с лямбдой
Narrowed(int, 'x > 0')            # call со строкой (можно было бы поддержать, но избыточно)
class Foo(Narrowed): ...          # subclassing
Narrowed[42, 'x > 0']             # неклассовый T
Narrowed[int]                      # arity
```

Каждая запрещённая форма даёт детерминированную ошибку на рантайме и (где применимо) в плагине.

**Почему `Narrowed(int, 'x > 0')` тоже запрещён** (call со строкой): чтобы не создавать четыре варианта одного API. Соответствие «B = call+lambda, E = subscript+string» жёсткое; нарушение → ошибка с указанием правильной формы.

## Целевое поведение

### Core

- Обе формы создают идентичный по поведению класс (см. §«Общая фабрика»).
- `isinstance(5, PositiveInt)` → True, `isinstance(-1, PositiveInt)` → False, `isinstance('1', PositiveInt)` → False — для обеих форм.
- `issubclass(PositiveInt, int)` → True (если T subclassable), `issubclass(int, PositiveInt)` → False.
- `PositiveInt(5)` → возвращает `5` (pass-through), после рантайм-проверки `simtypes.check(5, T) and pred(5)`. При несоответствии — `TypeError` с детерминированным текстом.
- `x: PositiveInt = 5` → ОК в mypy.
- `x: PositiveInt = -5` → mypy-ошибка `narrowing: predicate rejected literal -5` (literal narrowing, см. §«Mypy plugin: literal narrowing»).
- Композиция: `Narrowed(Narrowed(int, lambda x: x > 0), lambda x: x < 100)` и `Narrowed[Narrowed[int, 'x > 0'], 'x < 100']` — обе валидны, рекурсивно резолвятся.
- Кросс-композиция (`Narrowed(Narrowed[int, 'x > 0'], lambda x: x < 100)`) — допустима; внешняя форма независима от внутренней.

### Predicate body — единые правила для лямбды и строки

Тело лямбды (B) и Python-выражение внутри строки (E) проходят **один и тот же** AST-чекер. Различия только в источнике AST:
- B: `ast.parse(getsources.getsource(fn), mode='exec')` → ищем `ast.Lambda` → берём `body`.
- E: `ast.parse(string, mode='eval')` → берём `body` (`Expression.body`).

После извлечения AST правила проверки идентичны.

Допускаются `ast.Name(ctx=Load)` следующих категорий:

1. **Аргумент предиката** — для B это имя из `lambda x: ...` (любое валидное имя, обычно `x`); для E это всегда `x` (sentinel). Других вариантов в E нет: alias не поддерживается, чтобы не плодить опции.
2. **Builtins из allowlist** (фиксирован в `narrowing/_lambda_check.py`):
   `len`, `bool`, `int`, `float`, `str`, `bytes`, `tuple`, `list`, `dict`, `set`, `frozenset`, `type`, `isinstance`, `issubclass`, `hasattr`, `getattr`, `abs`, `min`, `max`, `sum`, `all`, `any`, `round`, `divmod`, `pow`, `repr`, `ord`, `chr`, `enumerate`, `zip`, `range`, `reversed`, `sorted`, `next`, `iter`, `True`, `False`, `None`, `NotImplemented`, `Ellipsis`.
3. **Имена, импортированные из stdlib**, при условии что объект, реально привязанный к имени в подходящих globals, является stdlib-модулем (`import X`) или прямым атрибутом stdlib-модуля (`from X import Y [as Z]`).

   **Источник globals для stdlib-резолва различается между формами**:
   - B: `fn.__globals__` (лямбда «помнит» свой модуль).
   - E: `sys._getframe(1).f_globals` в `__class_getitem__` — globals того модуля, откуда вызвали subscript. Стандартный паттерн (используется в `dataclasses`, `typing.get_type_hints`).

   На плагин-стороне для обеих форм globals реконструируются одинаково: парсим файл пользователя через `ast.parse(source)`, ходим по top-level `Import`/`ImportFrom`/`Assign`, строим mapping имён, учитывая shadowing.

   **Runtime-критерий валидности имени `name`** (общий для B и E):
   ```
   obj = caller_globals.get(name, _MISSING)
   если obj is _MISSING: reject
   если isinstance(obj, types.ModuleType) and obj.__name__ ∈ stdlib_names: accept
   для каждого mod_name in stdlib_names:
       mod = sys.modules.get(mod_name); если mod is None: continue
       если getattr(mod, name, _MISSING) is obj: accept
   reject
   ```

   Пример валидного предиката (обе формы):
   ```python
   import re
   from re import match

   # B
   EMAIL_B = Narrowed(str, lambda s: match(r'^[^@]+@[^@]+\.[^@]+$', s) is not None)
   # E
   EMAIL_E = Narrowed[str, "match(r'^[^@]+@[^@]+\\.[^@]+$', x) is not None"]
   ```

   Пример невалидного:
   ```python
   from re import match
   match = 42  # shadowing
   BAD_B = Narrowed(str, lambda s: match(r'.', s) is not None)
   BAD_E = Narrowed[str, "match(r'.', x) is not None"]
   ```

   Оба отвергаются по тому же правилу: `match` уже не attribute of `re`.

   **Python 3.8/3.9**: `sys.stdlib_module_names` отсутствует (появился в 3.10). Встраиваем frozen-список в `narrowing/_stdlib_names.py` (`STDLIB_MODULE_NAMES: FrozenSet[str]`).

**Независимое имя** = `ast.Name(ctx=Load)` в теле, стоящее само по себе. `lambda x: x.y > 5` — `Name('x')` (аргумент) OK, `y` — атрибут, не Name-нода. Аналогично для E: `'x.y > 5'` — OK.

Walrus `:=`, nested lambdas, comprehensions, `if-else`, generator expressions — разрешены; рекурсивно проверяются с расширенным scope.

### Допустимые формы T (расширено vs v3)

`simtypes.check(value, T)` принимает широкий набор форм для T (пользователь сказал «важны все эдж-кейсы simtypes»). `_validate_base(T)` принимает T если выполнено любое:

1. `inspect.isclass(T)` — обычный класс (subclassable или нет).
2. `T is None` — интерпретируется как `type(None)`.
3. `typing.get_origin(T) is not None` — параметризованный generic (`List[int]`, `Dict[str, int]`, `Optional[int]`, `Union[int, str]`).
4. `T is typing.Any` — Any.

Reject — callable, инстанс, не-typing форма с `get_origin() is None` и не-класс (`Narrowed(42, ...)` и т.п.).

**Subclassability**: для (1) проверяется через probe (`type('_probe', (T,), {})` в try/except). Для (2)–(4) всегда fallback (`bases=()`), потому что наследование от `None`/parameterized-generic/`Any` либо невозможно, либо семантически некорректно. Это означает: `issubclass(Narrowed(List[int], ...), list)` не работает (см. §«Известные ограничения»).

**Plugin-сторона** для (3) резолвит generic через `ctx.api.analyze_type` и работает с `Instance(origin, args)`. Для (1) — `Instance(class)`. Для (2) — `NoneType`. Для (4) — `AnyType`. Эти все штатно обрабатываются mypy.

### Типы, не допускающие наследования

`bool`, `NoneType`, ряд C-extension типов имеют `Py_TPFLAGS_BASETYPE = 0`. Для них fallback (`bases=()`), `__instancecheck__` через метакласс делает `simtypes.check + pred`. Ограничение: `issubclass(Narrowed(bool, p), bool)` не возвращает True — Python зовёт `type(bool).__subclasscheck__(bool, cls)` и мы не контролируем сторону `bool`. Документируется как known limitation.

### Предикат выбрасывает исключение

Пробрасываем наверх. `__instancecheck__` устроен как short-circuit: `simtypes.check(obj, T) and pred(obj)`. Если obj не T — pred не зовётся.

## Архитектура runtime (`narrowing/narrowed.py`)

### Метакласс

```python
from simtypes import check as _simtypes_check

class NarrowedMeta(type):
    _narrowing_base: object  # class | None | parameterized generic | Any
    _narrowing_pred: Callable[[object], object]
    _narrowing_repr_source: str  # для __repr__: либо source лямбды через getsources, либо строка из E

    def __instancecheck__(cls, instance: object) -> bool:
        # strict=True даёт content-валидацию для параметризованных T (List[int] и т.п.)
        if not _simtypes_check(instance, cls._narrowing_base, strict=True):
            return False
        return bool(cls._narrowing_pred(instance))

    def __subclasscheck__(cls, subclass: type) -> bool:
        return subclass is cls

    def __repr__(cls) -> str:
        # Форматирование значений T и predicate-source через printo (см. §«Зависимость printo»).
        # printo даёт более качественный repr для typing-форм (List[int], Optional[X])
        # и многострочных source'ов лямбды, чем встроенный repr().
        from printo import format as _printo_format
        return f"Narrowed[{_printo_format(cls._narrowing_base)}, {_printo_format(cls._narrowing_repr_source)}]"
```

`__repr__` использует subscript-стиль `Narrowed[T, '...']` независимо от того, через B или E создан класс — единая визуальная форма для отладки. Точная сигнатура `printo` уточняется на step 1 (mini-spike); приведённый код использует placeholder-имя `printo.format`. При расхождении — корректируется в имплементации.

### `Narrowed` — две точки входа в общую фабрику

```python
class Narrowed:
    def __new__(cls, *args: object, **kwargs: object) -> Type[object]:
        # B-форма: Narrowed(T, lambda)
        # Лямбда несёт свои __globals__, дополнительный frame-introspection не нужен.
        base, pred_callable, repr_source = _normalize_call_args(args, kwargs)
        return _build_narrowed_class(base, pred_callable, repr_source)

    def __class_getitem__(cls, params: object) -> Type[object]:
        # E-форма: Narrowed[T, 'expr']
        # Захватываем globals прямо здесь — frame-индексы фиксированы независимо от рефакторингов
        # _normalize_subscript_params (которая может позже звать вспомогательные функции).
        # frame[0] = __class_getitem__, frame[1] = user code.
        caller_globals = sys._getframe(1).f_globals
        base, pred_callable, repr_source = _normalize_subscript_params(params, caller_globals)
        return _build_narrowed_class(base, pred_callable, repr_source)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError('narrowing: Narrowed cannot be subclassed; use Narrowed(T, lambda) or Narrowed[T, "expr"]')
```

`__new__` возвращает не-`Narrowed`-объект (новый класс), поэтому `__init__` не зовётся (документированный Python-механизм).

### Общая фабрика `_build_narrowed_class`

```python
def _build_narrowed_class(
    base: object,
    pred: Callable[[object], object],
    repr_source: str,
) -> Type[object]:
    def __new__(inner_cls: Type[object], value: object) -> object:
        if not _simtypes_check(value, base, strict=True):
            raise TypeError(f"narrowing: expected {_format_base(base)}, got {type(value)!r}")
        if not bool(pred(value)):
            raise TypeError(f"narrowing: predicate rejected value {value!r}")
        return value  # pass-through

    namespace = {
        '_narrowing_base': base,
        '_narrowing_pred': staticmethod(pred),
        '_narrowing_repr_source': repr_source,
        '__new__': staticmethod(__new__),
    }
    bases: Tuple[type, ...] = (base,) if (inspect.isclass(base) and _is_subclassable(base)) else ()
    name = f'Narrowed[{_format_base(base)}, {repr_source!r}]'
    return NarrowedMeta(name, bases, namespace)
```

### `_normalize_call_args` (B-форма)

```python
def _normalize_call_args(args, kwargs):
    if kwargs:
        raise TypeError('narrowing: Narrowed does not accept keyword arguments')
    if len(args) != 2:
        raise TypeError(
            f'narrowing: expected Narrowed(T, predicate), got {len(args)} positional argument(s)'
        )
    base, pred = args
    _validate_base(base)
    if isinstance(pred, str):
        raise TypeError(
            'narrowing: string predicate is only valid in subscript form Narrowed[T, "expr"]; '
            'for call form, pass a lambda: Narrowed(T, lambda x: ...)'
        )
    _validate_lambda(pred)
    repr_source = _get_lambda_source(pred)  # через getsources
    return base, pred, repr_source
```

### `_normalize_subscript_params` (E-форма)

```python
def _normalize_subscript_params(params, caller_globals):
    # caller_globals принимаем параметром (захватывается в __class_getitem__),
    # чтобы не зависеть от sys._getframe-индексов при рефакторингах.
    if not isinstance(params, tuple):
        raise TypeError(
            f'narrowing: expected Narrowed[T, "expr"], got {params!r}'
        )
    if len(params) != 2:
        raise TypeError(
            f'narrowing: expected Narrowed[T, "expr"], got {len(params)} parameter(s)'
        )
    base, expr = params
    _validate_base(base)
    if not isinstance(expr, str):
        # Защита от Narrowed[int, lambda x: x > 0]
        if callable(expr) and not isinstance(expr, type):
            raise TypeError(
                'narrowing: lambda predicate is only valid in call form Narrowed(T, lambda x: ...); '
                'for subscript form, pass a string: Narrowed[T, "x > 0"]'
            )
        raise TypeError(
            f'narrowing: predicate must be a string, got {type(expr).__name__}'
        )
    pred_callable = _make_string_predicate(expr, caller_globals)
    return base, pred_callable, expr
```

### `_make_string_predicate`

```python
def _make_string_predicate(expr_str: str, caller_globals: Dict[str, object]) -> Callable[[object], object]:
    try:
        tree = ast.parse(expr_str, mode='eval')
    except SyntaxError as e:
        raise TypeError(f"narrowing: invalid predicate expression: {e}") from None

    _check_predicate_ast(tree.body, allowed_arg='x', caller_globals=caller_globals)

    code = compile(tree, '<narrowing predicate>', 'eval')
    eval_globals = _build_eval_globals(caller_globals)

    def pred(value):
        return eval(code, eval_globals, {'x': value})

    return pred
```

### `_format_base` (для `__repr__` и сообщений об ошибках)

```python
def _format_base(base: object) -> str:
    if base is None:
        return 'None'
    if base is typing.Any:
        return 'Any'
    qualname = getattr(base, '__qualname__', None)
    if qualname is not None:
        return qualname
    # typing-формы (List[int], Optional[int], Union[...]): repr читаем
    return repr(base)
```

Тесты покрывают: class, None, Any, parameterized generic (`List[int]`), Union, бескласс-инстансы (отрицательная ветвь — `_validate_base` отвергает).

### Валидаторы

- `_validate_base(T)` — проверки из §«Допустимые формы T».
- `_validate_lambda(fn)` — общая реализация в `narrowing/_lambda_check.py`:
  1. `getattr(fn, '__name__', None) == '<lambda>'`.
  2. `fn.__code__.co_argcount == 1`, `co_kwonlyargcount == 0`, нет `*args`/`**kw`, `__defaults__ is None`, `__kwdefaults__ is None`.
  3. `fn.__code__.co_freevars == ()`.
  4. **Bytecode-чек свободных имён** (primary path):
     - `referenced = set(code.co_names)`, `local = set(code.co_varnames)`.
     - Каждое `name` в `referenced` должно быть в `local ∪ BUILTINS_ALLOWLIST ∪ stdlib_resolved(fn.__globals__)`.
     - Не требует исходника, работает в REPL/exec.
  5. **AST-чек тела** (для подробных сообщений и плагин-стороны):
     - Через `getsources.getsource(fn)` (он же handles REPL и raises `UncertaintyWithLambdasError` для multi-lambda-on-same-line).
     - `ast.parse` → находим `ast.Lambda` через `getsources.getclearsource` → тело валидируем тем же `_check_predicate_ast`, что и E.
- `_check_predicate_ast(node, allowed_arg, caller_globals)` — общая для B и E:
  - Поддерживаемые ноды: `BoolOp`, `BinOp`, `UnaryOp`, `Compare`, `Call`, `Attribute`, `Subscript`, `IfExp`, `List`, `Tuple`, `Set`, `Dict`, `ListComp`, `SetComp`, `DictComp`, `GeneratorExp`, `Lambda`, `Constant`, `Name`, `Starred`, `Slice`, `NamedExpr`, `FormattedValue`, `JoinedStr`.
  - Любая `Name(Load)` должна быть в `{allowed_arg} ∪ walrus_locals ∪ comprehension_locals ∪ nested_lambda_args ∪ BUILTINS_ALLOWLIST ∪ stdlib_resolved(caller_globals)`.

### Сообщения об ошибках (детерминированные, для `full_match`)

- `'narrowing: expected Narrowed(T, predicate), got {n} positional argument(s)'`
- `'narrowing: expected Narrowed[T, "expr"], got {n} parameter(s)'`
- `'narrowing: Narrowed does not accept keyword arguments'`
- `'narrowing: first argument must be a class, got {value!r}'`
- `'narrowing: second argument must be a lambda, got {value!r}'` (для B)
- `'narrowing: predicate must be a string, got {type_name}'` (для E без callable-check)
- `'narrowing: lambda predicate is only valid in call form ...'` (для `Narrowed[int, lambda]`)
- `'narrowing: string predicate is only valid in subscript form ...'` (для `Narrowed(int, "...")`)
- `'narrowing: lambda must take exactly one positional argument'`
- `'narrowing: lambda must not have default values'`
- `'narrowing: lambda must not capture closure variables'`
- `'narrowing: predicate body references free name {name!r}'`
- `'narrowing: invalid predicate expression: {parse_error}'`
- `'narrowing: cannot disambiguate lambda at line {N} — put each Narrowed(...) on its own line'`

### `_is_subclassable`

```python
def _is_subclassable(T: type) -> bool:
    cached = _SUBCLASSABLE_CACHE.get(id(T))
    if cached is not None:
        return cached
    try:
        type('_probe', (T,), {})
        result = True
    except TypeError:
        result = False
    _SUBCLASSABLE_CACHE[id(T)] = result
    return result
```

## Type signatures `Narrowed` для статанализа без плагина

Если пользователь подключил `narrowing` как зависимость, но **не добавил** `narrowing.plugin` в `[tool.mypy] plugins`, mypy всё равно видит исходник `Narrowed` и пытается его типизировать. Без аккуратных сигнатур `__new__`/`__class_getitem__` он начнёт ругаться на любой `Narrowed(int, lambda x: x > 0)`. Чтобы baseline-поведение было разумным даже без плагина, сигнатуры пишутся inline в `narrowing/narrowed.py` через `typing.overload` и `TypeVar`:

```python
from typing import Any, Callable, Generic, Tuple, Type, TypeVar, overload

T = TypeVar('T')

class Narrowed(Generic[T]):
    @overload
    def __new__(cls, base: Type[T], pred: Callable[[T], object]) -> Type[T]: ...
    @overload
    def __new__(cls, *args: Any, **kwargs: Any) -> Type[Any]: ...  # fallback на ошибки рантайма
    def __new__(cls, *args, **kwargs):
        ...  # реальная реализация

    def __class_getitem__(cls, params: Tuple[Type[T], str]) -> Type[T]: ...
```

Без плагина:
- `Narrowed(int, lambda x: x > 0)` — mypy видит `Type[int]`. `isinstance(5, Narrowed(...))` валиден.
- `x: PositiveInt = -5` — mypy видит `PositiveInt: Type[int]`, не как TypeAlias → ругается «Variable not valid as type». **С плагином** этот же код работает корректно (плагин регистрирует synthetic TypeAlias).
- `Narrowed[int, 'x > 0']` — mypy видит `Type[int]`. Inline-использование работает; alias не работает без плагина.

Это намеренный baseline — без плагина теряется literal-narrowing и subscript-alias, но базовые операции остаются типизированными. README явно указывает: «for full feature set, install plugin».

**Stub-файл `.pyi`** не делаем — overloads inline в `narrowed.py` достаточны и поддерживают coverage 100% без особых трюков.

## Mypy-плагин (`narrowing/plugin.py`)

### Базовые возможности

1. **Entry point** через `[tool.mypy] plugins = ['narrowing.plugin']`.

2. **`get_dynamic_class_hook('narrowing.Narrowed')`** — для B-формы (`PositiveInt = Narrowed(int, lambda x: x > 0)`):
   - Срабатывает на `CallExpr` с targetом `Narrowed`.
   - Валидирует форму вызова, T, лямбду через общий `_lambda_check.py`.
   - Создаёт synthetic TypeInfo для alias'а (см. §«Constructor-literal: synthetic TypeInfo»). Подставляет underlying T как baseclass + правильный MRO.
   - Регистрирует TypeInfo в symbol table через `ctx.api.add_symbol_table_node`.
   - Сохраняет запись в `_PREDICATE_REGISTRY[alias_fqn]` + ссылку в `typeinfo.metadata['narrowing']`.

3. **`get_type_analyze_hook('narrowing.Narrowed')`** — для E-формы (`PositiveInt = Narrowed[int, 'x > 0']`):
   - Срабатывает на `UnboundType('Narrowed', ...)` — то есть когда mypy уже распарсил Subscript и пришёл резолвить.
   - Перехватывает **до** того, как mypy попытается разрешить deferred-string-args как forward references.
   - Читает `args[1].original_str_expr` (строку), парсит через `ast.parse`, валидирует через тот же `_check_predicate_ast`.
   - Создаёт synthetic TypeInfo для alias'а (тот же путь, что в п.2).
   - Регистрирует в symtab + `_PREDICATE_REGISTRY`.
   - **Зависит от спайка step 1**: если mypy парсит forward-ref-строку eagerly (до плагин-хука), хук не успевает; в этом случае E работает только в inline-позициях.

4. **`get_method_hook`** для `__init__`/`__new__` динамически созданного класса — ловит `PositiveInt(value)`:
   - Если `value` — `LiteralType[v]` и предикат компилировался без side-effects, evaluate и `ctx.api.fail` при False.
   - Если не literal — return T.

5. **Запрещённая форма `Narrowed[..., LambdaExpr]`** — плагин-перехвата не существует. `expr_to_unanalyzed_type` валится на C-уровне до любого хука, и подходящего хука в plugin API mypy 1.14–1.20 нет (полный список хуков: `get_type_analyze_hook`, `get_function_hook`, `get_method_hook`, `get_attribute_hook`, `get_class_attribute_hook`, `get_class_decorator_hook`, `get_metaclass_hook`, `get_base_class_hook`, `get_dynamic_class_hook`, `get_customize_class_mro_hook`, `get_additional_deps`, `report_config_data`, `get_function_signature_hook`, `get_method_signature_hook` — `get_subscript_hook` не существует). Полагаемся на mypy native `[valid-type]` ошибку. Typing-тест в `tests/typing/test_narrowed.py` проверяет факт fail'а (любой `[valid-type]`/`[misc]` ошибкой), без требования к точному тексту. Issue для апстрим mypy с предложением минимального API-расширения — в `issue.md` в корне репо.

### Хранение метаданных предиката

```python
_PREDICATE_REGISTRY: Dict[str, _PredicateRecord] = {}
# ключ — FQN type alias'а (например, 'mymodule.PositiveInt').
```

Для обеих форм (B и E) запись в `_PREDICATE_REGISTRY` идентична: `_PredicateRecord(base_type, predicate_callable, source_repr, predicate_ast)`. Плагин-side reconstruction предиката с stdlib-импортами одинаков:

1. **Источник source модуля**: предпочитаем `ctx.api.modules[mod].source` (in-memory, заполнен mypy при analysis) — это работает и для `mypy.api.run(source=...)`, и для обычного file-based прогона. Fallback на чтение файла через `path` только если `source is None`.
2. Парсим source через `ast.parse`, строим mapping имён + shadowing (top-level `Import`/`ImportFrom`/`Assign`).
3. `eval_globals = BUILTINS_ALLOWLIST ∪ {resolved stdlib mappings}` — реальные модули/атрибуты подгружаются через `importlib.import_module` на плагин-стороне.
4. Компилируем body предиката (для B — извлечённый из лямбды через `getsources.getclearsource` + AST; для E — `ast.parse(string, mode='eval')`) → code object.
5. Predicate callable: `lambda value: eval(code, eval_globals, {'x': value})` (имя arg'а: `x` для E всегда, для B — то, что у пользователя).

### Mypy plugin: literal narrowing на присваивании `x: PositiveInt = -5`

Реализуется через monkey-patch `TypeChecker.check_assignment`. Логика идентична для B и E — отличие только в источнике записи в `_PREDICATE_REGISTRY`.

См. полное описание в v3-плане; механизм не меняется. Self-test (`tests/units/test_plugin_compatibility.py`) расширяется для покрытия обеих форм.

#### Forward-substitution

```python
a = 5
x: PositiveInt = a  # mypy выводит a: Literal[5], patched check_assignment получает Literal[5]
```

Бесплатно, как в v3.

### Constructor-literal: `PositiveInt(-5)` через synthetic TypeInfo

**Проблема plain-Instance-подхода**: если в `get_dynamic_class_hook` просто вернуть `Instance(int)`, mypy видит `PositiveInt` как обычный `int`. `get_method_hook('mymodule.PositiveInt.__new__')` не сработает — у Instance(int) TypeInfo от builtin'а `int`, а не наш custom. Не за что зацепиться.

**Решение — synthetic TypeInfo для каждого alias'а**. Прецедент в mypy кодовой базе: `TypedDictPlugin`, `EnumPlugin`, `NamedTuplePlugin` — все создают свои TypeInfo поверх обычных классов через `add_symbol_table_node`.

**Алгоритм** (применяется в `get_dynamic_class_hook` для B и в `get_type_analyze_hook` для E):

1. **Создание TypeInfo**:
   ```python
   from mypy.nodes import TypeInfo, ClassDef, Block, SymbolTable, SymbolTableNode, GDEF
   from mypy.types import Instance

   defn = ClassDef(alias_name, Block([]))
   defn.fullname = f'{ctx.api.cur_mod_id}.{alias_name}'
   new_typeinfo = TypeInfo(SymbolTable(), defn, ctx.api.cur_mod_id)
   new_typeinfo._fullname = defn.fullname
   defn.info = new_typeinfo
   ```

2. **Прописывание baseclass и MRO**:
   - Для simple T (`int`, `str`, кастомный класс): `bases = [Instance(T_typeinfo, [])]`, `mro = [new_typeinfo, *T_typeinfo.mro]`. Получается, например, `[PositiveInt, int, object]`.
   - Для parameterized generic T (`List[int]`): `bases = [Instance(list_typeinfo, [Instance(int_typeinfo)])]`, MRO от list.
   - Для non-subclassable T (`bool`, `NoneType`): mypy не делает рантайм-subclass-check'и, поэтому MRO формируется так же, как для subclassable. Mypy примет как валидный subtype.
   - Для `T = Any`: `bases = [AnyType()]`-эквивалент через специальную форму; на практике такой alias бесполезен, но не должен ронять плагин.
   - Для `T = None` (NoneType): `bases = [Instance(NoneType_typeinfo, [])]`.

3. **Регистрация в symtab**:
   ```python
   ctx.api.add_symbol_table_node(
       alias_name,
       SymbolTableNode(GDEF, new_typeinfo),
   )
   ```

4. **Привязка предиката к TypeInfo**:
   ```python
   new_typeinfo.metadata['narrowing'] = {'alias_fqn': new_typeinfo._fullname}
   _PREDICATE_REGISTRY[new_typeinfo._fullname] = _PredicateRecord(base_type, predicate_callable, repr_source, predicate_ast)
   ```
   `metadata['narrowing']` сериализуется в incremental cache штатно (mypy умеет хранить плагин-метаданные на TypeInfo). При cache miss `_PREDICATE_REGISTRY` перезаполняется на reanalysis.

5. **Возврат типа из hook'а**: `Instance(new_typeinfo)`. Хук возвращает наш TypeInfo, не underlying T. `reveal_type(x)` покажет `mymodule.PositiveInt` (точнее, чем `int`).

6. **`get_method_hook(f'{alias_fqn}.__new__')`** — теперь срабатывает на `PositiveInt(-5)`:
   ```python
   def get_method_hook(self, fullname: str):
       if fullname in _PREDICATE_REGISTRY_KEYS_NEW_METHODS:
           return _check_constructor_literal
       return None

   def _check_constructor_literal(ctx: MethodContext) -> Type:
       alias_fqn = _strip_dot_new(ctx.callee_full_name)  # убираем '.__new__'
       record = _PREDICATE_REGISTRY[alias_fqn]
       if len(ctx.args) != 1 or len(ctx.args[0]) != 1:
           return ctx.default_return_type
       arg_type = ctx.arg_types[0][0]
       if isinstance(arg_type, LiteralType):
           try:
               passed = bool(record.predicate(arg_type.value))
           except Exception:
               return ctx.default_return_type  # предикат упал на статическом исполнении — runtime разберётся
           if not passed:
               ctx.api.fail(
                   f'narrowing: predicate rejected literal {arg_type.value!r}',
                   ctx.context,
                   code=errorcode_narrowing,
               )
       return ctx.default_return_type  # Instance(new_typeinfo)
   ```

**Подводные камни**:

- **MRO с C-extension типами**: для `int`, `str` mypy имеет TypeInfo в builtins. Мы ссылаемся на `T_typeinfo` через `ctx.api.named_type('builtins.int').type` или аналог. Для пользовательских классов — через `ctx.api.lookup_qualified`.
- **Incremental cache**: `metadata['narrowing']` хранится в плагин-метаданных TypeInfo. При повторном запуске mypy `_PREDICATE_REGISTRY` пуст, но `alias_fqn` в metadata есть. Hook на `__new__` лукапит → находит ключ в metadata → если в registry нет, зовёт `_repopulate_from_metadata(alias_fqn)`, который реконструирует predicate из source-файла.
- **Cross-module aliases**: если `PositiveInt` определён в `module_a` и используется в `module_b`, плагин в `module_b` видит alias через imported TypeInfo. `metadata['narrowing']['alias_fqn']` остаётся `module_a.PositiveInt` → лукап и repopulation работают.
- **`ctx.api` interface для `add_symbol_table_node`**: на разных версиях mypy сигнатура слегка разнится. На 1.14 — `SemanticAnalyzerInterface.add_symbol_table_node(name: str, stnode: SymbolTableNode) -> bool`. На 1.20 — то же. Стабильно.

**Объём кода**: synthetic TypeInfo + MRO calculation + serialization repopulation — оценочно ~80–120 строк плагин-кода (vs ~10 для plain Instance). Принято — пользователь подтвердил, scope не сужаем.

### Nested

`Narrowed(Narrowed(int, p1), p2)` и `Narrowed[Narrowed[int, 'p1'], 'p2']` — рекурсия по форме до достижения non-Narrowed T.

### Plugin ошибки и коды

`ctx.api.fail(msg, ctx.context, code=errorcode_narrowing)`. Custom error code `ErrorCode('narrowing', 'Narrowing usage', 'narrowing')` через standard mechanism (mypy 1.17+ поддерживает регистрацию error code из плагина в config-options; на 1.14 работает регистрация без config-toggle).

## Tests

### `tests/units/test_narrowed.py`

Существующие 2 теста переписать на B-форму (call-syntax). Плюс E-форма как параллельный набор где это имеет смысл.

**Для каждого core-кейса параметризуем по форме**:
```python
@pytest.fixture(params=['B', 'E'])
def make_narrowed(request):
    if request.param == 'B':
        def make(base, src): return Narrowed(base, eval(f'lambda x: {src}'))
    else:
        def make(base, src): return Narrowed[base, src]
    return make
```

Каждый из ~20 общих кейсов прогоняется через обе формы.

**B-only тесты** (специфика лямбды):
- Closure: `def outer(): y=5; return lambda x: x > y`, `Narrowed(int, outer())` → reject.
- REPL/exec лямбда (bytecode-only path): `exec('f = lambda x: x > 0', g)`, `Narrowed(int, g['f'])` → OK.
- Несколько лямбд на одной строке: `n1, n2 = Narrowed(int, lambda x: x > 0), Narrowed(int, lambda x: x < 0)` → обе OK; третья форма с конфликтным getsources: ловим `UncertaintyWithLambdasError`, fail с понятным сообщением.

**E-only тесты** (специфика строки):
- Невалидный Python в строке: `Narrowed[int, 'x >']` → `TypeError: invalid predicate expression: ...`.
- Multiline: `Narrowed[int, 'x > 0\nand x < 100']` → OK (newline в Python-строке валиден для `ast.parse`).
- Empty string: `Narrowed[int, '']` → reject (no expression).
- Имя кроме `x`: `Narrowed[int, 'value > 0']` → reject (`value` не в allowed).

**Cross-form forbidden**:
- `Narrowed[int, lambda x: x > 0]` → `TypeError: lambda predicate is only valid in call form...`.
- `Narrowed(int, 'x > 0')` → `TypeError: string predicate is only valid in subscript form...`.

**Cross-form nested** (B внутри E и наоборот):
- `Narrowed(Narrowed[int, 'x > 0'], lambda x: x < 100)` — внутри B-формы используется E-class. Внешняя проверка через лямбду, внутренняя через строку. Оба предиката должны вызываться при `isinstance(50, ...)` → True, `isinstance(-5, ...)` → False, `isinstance(150, ...)` → False.
- `Narrowed[Narrowed(int, lambda x: x > 0), 'x < 100']` — обратная композиция. Аналогично.
- В обоих кейсах `_format_base` для внутреннего Narrowed-класса даёт его `__qualname__` (NarrowedMeta-сформированный) — проверяем `__repr__` стабилен и читается.

**Validation errors** (общие, через `pytest.raises(TypeError, match=...)` + `full_match`):
- arity, kwargs, не-класс T, не-лямбда/не-строка predicate.
- Свободный глобал, не-whitelist builtin, shadowing stdlib имени.
- Walrus OK, nested-lambda OK, attribute OK.

**Stdlib через obe формы**:
- `import re; Narrowed(str, lambda s: re.match('.', s) is not None)` — OK.
- `import re; Narrowed[str, "re.match('.', x) is not None"]` — OK.

**Допустимые формы T** (расширено):
- `Narrowed(List[int], lambda l: len(l) > 0)` — OK, simtypes валидирует контент.
- `Narrowed(Optional[int], lambda x: x is None or x > 0)` — OK.
- `Narrowed(type(None), lambda x: x is None)` — OK через fallback.
- `Narrowed(Any, lambda x: True)` — OK.

**Конструктор**:
- `PositiveInt(5) == 5` + `type(PositiveInt(5)) is int`.
- `PositiveInt(-5)` → TypeError.
- `PositiveInt('5')` → TypeError.
- Для fallback-типов: `NoneTypeNarrowed(None) is None` → True.

**issubclass**:
- `issubclass(Narrowed(int, p), int)` → True.
- `issubclass(Narrowed(int, p), object)` → True.
- `issubclass(int, Narrowed(int, p))` → False.
- Reflexive — True.
- `issubclass(Narrowed(bool, p), bool)` — xfail (known limitation).
- `issubclass(Narrowed(List[int], p), list)` — False (parameterized → fallback).

Итого ≈55 тестов с параметризацией.

### `tests/units/test_plugin_compatibility.py`

Self-test для monkey-patch `check_assignment`. Прогоняет через `mypy.api.run`:
- Positive B: `PositiveInt = Narrowed(int, lambda x: x > 0); x: PositiveInt = 5` → 0 errors.
- Negative B: `... x: PositiveInt = -5` → 1 error с текстом predicate-rejected-literal.
- Positive E (если spike прошёл): `PositiveInt = Narrowed[int, 'x > 0']; x: PositiveInt = 5` → 0 errors.
- Negative E: то же с `-5` → 1 error.

Self-test громко падает при разъезде (понятное сообщение про возможный change в mypy internals).

**Coverage of safe-fail branch**: в `_assignment_hook.py` patched-функция оборачивает `_narrowing_post_check` в `try/except Exception → _emit_patch_failure_warning`. Чтобы покрыть except-ветку (требование 100% coverage), пишем отдельный тест, который **искусственно провоцирует** исключение в post-check'е:

```python
def test_assignment_patch_safefail_logs_warning(monkeypatch):
    import warnings
    from narrowing import _assignment_hook
    monkeypatch.setattr(
        _assignment_hook,
        '_narrowing_post_check',
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('synthetic')),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        # вызываем patched check_assignment через mypy.api.run на минимальном кейсе
        ...
    assert any('synthetic' in str(w.message) for w in caught)
```

Без этого теста except-ветка не покрывается естественным path'ом и coverage упадёт ниже 100%.

### `tests/typing/test_narrowed.py`

```python
import pytest

# B-форма
@pytest.mark.mypy_testing
def mypy_b_positive_simple():
    from narrowing import Narrowed
    PositiveInt = Narrowed(int, lambda x: x > 0)
    x: PositiveInt = 5
    reveal_type(x)  # R: builtins.int

@pytest.mark.mypy_testing
def mypy_b_negative_assignment_literal():
    from narrowing import Narrowed
    PositiveInt = Narrowed(int, lambda x: x > 0)
    x: PositiveInt = -5  # E: narrowing: predicate rejected literal -5

# E-форма (только если spike прошёл; иначе помечены xfail)
@pytest.mark.mypy_testing
def mypy_e_positive_simple():
    from narrowing import Narrowed
    PositiveInt = Narrowed[int, 'x > 0']
    x: PositiveInt = 5
    reveal_type(x)  # R: builtins.int

@pytest.mark.mypy_testing
def mypy_e_negative_assignment_literal():
    from narrowing import Narrowed
    PositiveInt = Narrowed[int, 'x > 0']
    x: PositiveInt = -5  # E: narrowing: predicate rejected literal -5

# Forbidden cross-form
@pytest.mark.mypy_testing
def mypy_forbidden_subscript_lambda():
    from narrowing import Narrowed
    Bad = Narrowed[int, lambda x: x > 0]  # E: <any error from mypy or our plugin>

@pytest.mark.mypy_testing
def mypy_forbidden_call_string():
    from narrowing import Narrowed
    Bad = Narrowed(int, 'x > 0')  # E: narrowing: string predicate is only valid in subscript form ...

# Negative shape (общее)
@pytest.mark.mypy_testing
def mypy_negative_missing_predicate_b():
    from narrowing import Narrowed
    Bad = Narrowed(int)  # E: narrowing: expected Narrowed(T, predicate), got 1 positional argument(s)

@pytest.mark.mypy_testing
def mypy_negative_missing_predicate_e():
    from narrowing import Narrowed
    Bad = Narrowed[int]  # E: narrowing: expected Narrowed[T, "expr"], got 1 parameter(s)

# ... ещё ~10 кейсов с обеими формами для core, nested, stdlib, ctor-literal, free-name.
```

Итого ≈25 typing-тестов (≈12 для каждой формы + cross-form-forbidden).

### `tests/documentation/test_readme.py`

Пустой на v1; doctest заполнится после step 10 (README).

## Config-правки

### `pyproject.toml`

1. `dependencies = ['simtypes>=0.0.13,<0.1', 'getsources>=0.0.4,<0.1', 'printo>=<TBD>,<0.1']`. Версии — последние стабильные на момент сверки. Pin диапазон до следующего minor (`<0.1`) — пакеты на ранних версиях, ломающие изменения возможны. Точный pin для `printo` — на step 1 mini-spike.
2. `[tool.mypy] plugins = ['narrowing.plugin']`.

### CLAUDE.md

Обновить «Zero runtime dependencies» → «Runtime dependencies: simtypes (deep type checking), getsources (lambda source extraction for B-form), printo (repr formatting for synthetic classes)».

### Coverage

Плагин покрывается прогоном `pytest-mypy-testing` + `test_plugin_compatibility.py`. **`omit` плагина из coverage запрещён** (противоречит 100% инварианту из CLAUDE.md). При пробелах — subprocess-coverage через `COVERAGE_PROCESS_START` + `coverage.process_startup()`.

### README

Документировать:
- Обе формы синтаксиса (B и E) с примерами и обоснованием когда какая удобнее.
- Allowlist builtins — **ссылка на `narrowing/_lambda_check.py:BUILTINS_ALLOWLIST`** + 5–7 представительных примеров. Полный список из 40+ имён в README не дублируем (он рос бы при изменениях кода и быстро рассинхронизировался).
- stdlib-резолв и его правила (с примером `import re` + lambda/string).
- Запрещённые формы и тексты ошибок.
- Ограничения: non-subclassable T, parameterized-T, bool/issubclass.
- Установка mypy-плагина: `[tool.mypy] plugins = ['narrowing.plugin']`.
- **Caveat про dmypy**: после установки плагина или после изменения версии `narrowing` нужно `dmypy restart`, иначе daemon продолжает использовать старую версию плагина. mypy не делает реимпорт плагина автоматически.
- Поведение без плагина: базовая типизация (`Narrowed(int, lambda x: x > 0)` → `Type[int]`) работает; literal-narrowing и subscript-alias не работают. Плагин — required для full feature set.

## Порядок работ (TDD)

**Step 0 (DONE)**: Сверка API simtypes выполнена. Findings:
- Версия: simtypes 0.0.13 (Mar 17, 2026), на PyPI как `simtypes`.
- Сигнатура: `check(value, type_or_annotation, strict=False, pass_mocks=True) -> bool`.
- `strict=True` — content-validation для контейнеров (`check(['a'], List[str], strict=True) -> True`), **не** strict-bool/int семантика.
- Зафиксировать pin: `simtypes>=0.0.13,<0.1`.

**Step 1 (DONE)**: Спайки выполнены. Findings:

- **B-spike (hard gate)** — ✓. `get_dynamic_class_hook('narrowing.Narrowed')` срабатывает на `Narrowed(int, lambda x: x > 0)`, mypy 1.14.1 не падает на лямбде в CallExpr. Synthetic TypeInfo корректно регистрируется через `add_symbol_table_node(name, SymbolTableNode(GDEF, info))`, `reveal_type` показывает наш FQN. Доп. note: при инициализации synthetic info нужно `info.bases = [Instance(base, [])]; info.mro = [info, *base.mro]` — этого достаточно, чтобы mypy принял subtyping. Литерал-валидация типа (e.g. `x: PositiveInt = 5` принимает int как валидный subtype) требует доп. настройки synthetic info (наследование от `int`-via-Instance не делает int автоматически assignable в позицию PositiveInt — нужен `Liskov`-friendly подход; решается на step 6).

- **E-spike (soft gate)** — ✓. `get_type_analyze_hook('narrowing.Narrowed')` срабатывает на `Narrowed[int, 'x > 0']`. mypy передаёт `args[0]` = `UnboundType('int')` (резолвим через `ctx.api.analyze_type`), `args[1]` = **`RawExpressionType(literal_value='x > 0')`** — строка достаётся через атрибут `literal_value`, **не** `original_str_expr` (оба атрибута существуют на разных нодах, для нас работает первый). Это значит alias-form **работает** на компилированном mypy.

- **simtypes-microspike** — ✓. Сигнатура: `check(value, type_hint, strict=False, lists_are_tuples=False, pass_mocks=True) -> TypeIs[ExpectedType]` (важно: возврат `TypeIs`, не `bool` — это улучшает type-narrowing на стороне mypy при использовании `simtypes.check` в коде пользователя). Поведение:
  - `check(True, int) = True` (как isinstance — `True is int`).
  - `check(1, bool) = False` (асимметрично — simtypes защищает от bool-as-int-leak в обратном направлении). Это полезный edge case для нас, его покрываем тестами.
  - `check(None, type(None)) = True`, `check(None, None) = True` (поддержка `None`-as-type).
  - `check([1, 'a'], List[int], strict=True) = False` (content-validation работает в strict-режиме).

- **getsources-microspike** — ✓ для file-based кода (что и требуется в реальном использовании). REPL/stdin/heredoc → `OSError: could not extract source code` (ожидаемо, документированный edge case). Multi-line → `UncertaintyWithLambdasError` с понятным сообщением. **Python 3.8 не проверен** — в локальном venv стоит 3.10, а CI прогоняет 3.8–3.15; bytecode-only path остаётся primary, AST/source-path не критичен на 3.8.

- **printo-microspike** — ✓. Использовать `printo.superrepr(value)` для форматирования T и source в `__repr__`. `describe_call`/`describe_data_object` — для call-style (нам не подходит, у нас subscript-style). Версия 0.0.27, install name `printo`, на PyPI.

**Импликации для имплементации**:
- `_validate_lambda` primary path = bytecode (`co_names`/`co_varnames`/`co_freevars`); AST-path = optional, через getsources, для подробных сообщений.
- E-форма: достаём строку через `args[1].literal_value` (на 1.14.1 — атрибут на `RawExpressionType`).
- B-форма: synthetic TypeInfo создаётся в hook'е; для корректной приёмки литералов (`x: PositiveInt = 5`) требуется правильно настроенный MRO + `bases = [Instance(base, [])]` — детально проверим на step 6.
- simtypes: `bool`-asymmetry — фича, не баг; покрываем тестами с обеих сторон.
2. Переписать существующие 2 теста на B-форму (или E, на выбор пользователя). Написать остальные runtime-тесты (обе формы, параметризованно).
3. Реализовать `narrowing/narrowed.py` + `narrowing/_lambda_check.py` + `narrowing/_stdlib_names.py`.
4. Прогнать рантайм-тесты до green.
5. Написать typing-тесты (B + E + forbidden cross-form).
6. Реализовать `narrowing/plugin.py` — `get_dynamic_class_hook` + `get_type_analyze_hook` (если E-spike прошёл) + `get_method_hook` для constructor-literal.
7. Добавить `[tool.mypy] plugins` и `dependencies` в pyproject.
8. Literal-narrowing-on-annotation: `narrowing/_assignment_hook.py` (monkey-patch) + `_PREDICATE_REGISTRY`. Прогнать typing-тесты + `test_plugin_compatibility.py` до green.
9. Полный verification-прогон (см. §«Верификация»).
10. README.

## Верификация (end-to-end)

```
uv pip install --system -r requirements_dev.txt
uv pip install --system .
coverage run -m pytest -n auto --cache-clear --assert=plain
coverage report -m --fail-under=100 --omit='*tests*'
ruff check narrowing
ruff check tests
mypy --show-error-codes --strict --disallow-any-decorated --disallow-any-explicit --disallow-any-expr --disallow-any-generics --disallow-any-unimported --disallow-subclassing-any --warn-return-any narrowing
mypy --exclude '^tests/typing/' tests
```

Все zero errors на Python 3.8–3.15 и free-threaded 3.14t.

## Критические файлы

- `narrowing/narrowed.py` — `Narrowed.__new__` (B) + `__class_getitem__` (E) + общая фабрика + метакласс.
- `narrowing/_lambda_check.py` (новый) — общий bytecode/AST-чекер тела предиката, переиспользуется обеими формами и плагином.
- `narrowing/_stdlib_names.py` (новый) — frozen-копия `sys.stdlib_module_names` для 3.8/3.9.
- `narrowing/plugin.py` — `get_dynamic_class_hook` (B) + `get_type_analyze_hook` (E) + `get_method_hook` (ctor-literal) + `get_subscript_hook` (forbidden lambda-in-subscript).
- `narrowing/_assignment_hook.py` (новый) — monkey-patch `TypeChecker.check_assignment`.
- `tests/units/test_narrowed.py` — переписанные 2 + ~53 новых.
- `tests/units/test_plugin_compatibility.py` (новый) — self-test monkey-patch на B и E.
- `tests/typing/test_narrowed.py` — pytest-mypy-testing.
- `pyproject.toml` — `dependencies` + `[tool.mypy] plugins`.
- `README.md`, `CLAUDE.md`.

## Known risks / открытые вопросы

- **B-spike fail** — критично. Без B проект не имеет смысла.
- **E-spike fail** — soft. Если форма E не работает в alias-позиции, это не катастрофа: B остаётся рабочим, E работает только inline. Снижает ценность E, но не блокер.
- **Monkey-patch `check_assignment`** на разных версиях mypy — митигируется self-test'ом.
- **`issubclass(Narrowed(bool, p), bool)`**, `issubclass(Narrowed(List[int], p), list)` — known limitations.
- **`sys.stdlib_module_names` на 3.8/3.9** — frozen-список.
- **`getsources` на 3.8** — sub-spike step 1.
- **`printo` API и поддержка 3.8** — sub-spike step 1. PyPI и GitHub были недоступны (502) при попытке сверить API на момент написания плана; точная сигнатура и pin фиксируются на step 1. Если printo не подходит / не поддерживает 3.8 — fallback на встроенный `repr` через `_format_base`.
- **simtypes семантика для `True` vs `int`** — `simtypes.check(True, int)` возможно вернёт True (как `isinstance`); если для пользователя нужна strict-bool-семантика, придётся обернуть. Уточнение по факту первых тестов.
- **dmypy daemon** — patch применяется при импорте модуля плагина; daemon перезагружает плагины при изменении конфигурации, но не при первом запуске после установки → документировать.
- **FQN aliases внутри функций/классов** — на v1 поддерживается только module-level `PositiveInt = ...`; nested aliases молча не получают literal-narrowing (но runtime работает). Документируется.

## mypy issue

Параллельно с реализацией готовится issue для апстрим mypy с предложением минимального API-расширения для поддержки оригинального синтаксиса `Narrowed[int, lambda x: x > 0]` (без E-обходного пути). См. `issue.md` в корне репозитория.
