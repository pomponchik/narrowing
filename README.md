# narrowing

Predicate-based type narrowing for Python — class-like type aliases with
runtime validation predicates that work both at runtime and under mypy.

```python
from narrowing import Narrowed

PositiveInt = Narrowed(int, lambda x: x > 0)

isinstance(5, PositiveInt)   # True
isinstance(-1, PositiveInt)  # False
isinstance('1', PositiveInt) # False

x: PositiveInt = 5     # ok
y: PositiveInt = -5    # mypy error: predicate rejected literal -5
```

## Two equivalent syntactic forms

`narrowing` accepts predicates in two forms; both build identical classes:

```python
# B-form (call) — primary, mypy-validated
PositiveInt = Narrowed(int, lambda x: x > 0)

# E-form (subscript with string) — equivalent
PositiveInt = Narrowed[int, 'x > 0']
```

Inside the string form the variable `x` is the predicate's argument by
convention — it mirrors `lambda x: x > 0` exactly.

Mixed forms (`Narrowed[int, lambda]` or `Narrowed(int, 'x > 0')`) are
explicitly rejected with a message pointing at the right one.

## Installation

```bash
pip install narrowing
```

For full feature support — including assignment-literal narrowing
(`x: PositiveInt = -5` → mypy error) — install the *pure-Python* mypy:

```bash
pip install --no-binary mypy mypy
```

Then enable the plugin in your `pyproject.toml`:

```toml
[tool.mypy]
plugins = ['narrowing.plugin']
```

## Predicate body — what's allowed

The predicate body is a small Python expression. To keep the predicate
deterministic and analyzable, the body may reference only:

1. **Its argument** — `x` for E-form, the lambda's argument name for B-form.
2. **A fixed allowlist of builtins** — see `narrowing/_lambda_check.py`
   (`BUILTINS_ALLOWLIST`). It includes the common pure ones: `len`, `bool`,
   `int`, `float`, `str`, `bytes`, `list`, `dict`, `set`, `len`, `min`, `max`,
   `abs`, `sum`, `all`, `any`, `isinstance`, `True`, `False`, `None`, etc.
3. **Stdlib names imported in the surrounding module**, identified by
   *object identity* against `sys.modules`:

   ```python
   import re
   from re import match

   EmailRx = Narrowed(str, lambda s: re.match(r'^[^@]+@[^@]+$', s) is not None)
   EmailRx = Narrowed[str, "re.match(r'^[^@]+@[^@]+$', x) is not None"]
   EmailRx = Narrowed[str, "match(r'^[^@]+@[^@]+$', x) is not None"]
   ```

   If an imported name is later reassigned (`match = something_else`),
   the identity check fails and the predicate is rejected.

Anything else in the body — a free user variable, a non-stdlib import, a
non-allowlisted builtin — produces `TypeError` at construction time and
a mypy error at type-check time.

```python
y = 5
Bad = Narrowed(int, lambda x: x > y)
# TypeError: narrowing: predicate body references free name 'y'
```

## Composition

Both forms compose naturally:

```python
Bounded = Narrowed(Narrowed(int, lambda x: x > 0), lambda x: x < 100)
Bounded = Narrowed[Narrowed[int, 'x > 0'], 'x < 100']
Bounded = Narrowed(Narrowed[int, 'x > 0'], lambda x: x < 100)  # mixed forms ok
```

`isinstance(50, Bounded)` evaluates both predicates.

## Generic and complex `T`

The base type can be a class, a parameterized generic, `None` (for `NoneType`),
or `typing.Any`. Container content is validated through `simtypes` in strict
mode:

```python
from typing import List, Optional

NonEmptyIntList = Narrowed(List[int], lambda l: len(l) > 0)
isinstance([1, 2, 3], NonEmptyIntList)   # True
isinstance(['a', 'b'], NonEmptyIntList)  # False — content type-mismatch

NoneOrPositive = Narrowed(Optional[int], lambda x: x is None or x > 0)
isinstance(None, NoneOrPositive)  # True
isinstance(-5,   NoneOrPositive)  # False
```

## Constructor — pass-through validation

Calling the alias as a constructor validates and returns the same value:

```python
PositiveInt(5)   # 5 (same object)
PositiveInt(-5)  # TypeError: predicate rejected value -5
PositiveInt('5') # TypeError: expected int, got <class 'str'>
```

## Known limitations

- **Compiled mypy + assignment-literal narrowing**. The default
  `pip install mypy` is a mypyc-compiled binary; some of its call sites are
  direct C calls that bypass our Python-level patch on
  `TypeChecker.visit_assignment_stmt`. As a result, `x: PositiveInt = -5`
  is *not* statically rejected on compiled mypy. The plugin emits a one-time
  warning when this is detected. Install pure-Python mypy
  (`pip install --no-binary mypy mypy`) to get full literal narrowing.

- **Non-subclassable bases**. `bool`, `NoneType` and a few CPython C types
  set `Py_TPFLAGS_BASETYPE = 0`. We use a metaclass fallback so `isinstance`
  works correctly, but `issubclass(Narrowed(bool, p), bool)` returns False
  (Python dispatches `__subclasscheck__` on `bool` itself, which we can't
  override).

- **Parameterized generic `T` and `issubclass`**. `issubclass(Narrowed(List[int], p), list)`
  is False — when `T` isn't a plain class, the alias doesn't inherit from it.
  `isinstance` with content validation still works.

- **dmypy daemon**. After installing or upgrading `narrowing`, run
  `dmypy restart` — the daemon caches plugin code and won't pick up changes
  without a restart.

- **Local aliases inside functions**. Aliases declared inside a function or
  class body work at runtime and as types, but the literal-narrowing hook
  resolves them by FQN suffix; collisions across functions in the same
  module fall back to the module-level alias. Keep aliases module-level for
  guaranteed precision.

## Without the mypy plugin

If you depend on `narrowing` but haven't installed the plugin
(`[tool.mypy] plugins`), the basic typing still works through the inline
overload signature:

- `Narrowed(int, lambda x: x > 0)` is typed as `Type[int]`.
- `isinstance(x, Narrowed(...))` works at runtime.
- `x: PositiveInt = ...` produces `Variable not valid as a type` — install
  the plugin for alias support.

## Issue with mypy upstream

The original syntax `Narrowed[int, lambda x: x > 0]` (lambda directly inside
subscript, no string quote) cannot be supported on compiled mypy because
`expr_to_unanalyzed_type` raises `TypeTranslationError` on `LambdaExpr` at the
C level. We've drafted an upstream API extension proposal in `issue.md` of
this repository — please review or comment if you have an interest in
unblocking the natural form.
