# Plugin support for non-type expressions in subscript args (e.g. lambdas) for dynamic type aliases

## Summary

Third-party libraries that build runtime+statically-validated types want to expose a generic-like subscript syntax with arbitrary Python expressions as parameters, e.g.:

```python
PositiveInt = Narrowed[int, lambda x: x > 0]
```

This currently fails on **compiled mypy** because `expr_to_unanalyzed_type` raises `TypeTranslationError` for `LambdaExpr` (and most other non-type AST nodes) when they appear inside subscript arguments. The error is raised at C-level (the function is mypyc-compiled), which means Python-level monkey-patching from a plugin cannot intercept it.

This issue proposes minimal plugin API extensions that would allow such libraries to support the subscript syntax cleanly.

## Concrete use case

A library called `narrowing` provides predicate-based type narrowing — a class plus runtime predicate, validated both at runtime and statically:

```python
from narrowing import Narrowed

PositiveInt = Narrowed[int, lambda x: x > 0]
EmailStr   = Narrowed[str, lambda s: '@' in s]

isinstance(5, PositiveInt)   # True
isinstance(-1, PositiveInt)  # False

x: PositiveInt = -5          # mypy should error: predicate rejected literal -5
y: PositiveInt = 5           # ok
```

The library has a mypy plugin that:
- validates the predicate's AST (free names, builtin allowlist, stdlib imports);
- evaluates the predicate against `LiteralType` values for static rejection of obviously-bad literals;
- propagates the underlying type so `reveal_type(y)` shows `int`.

The plugin works for the **call form** `Narrowed(int, lambda x: x > 0)` via `get_dynamic_class_hook`. The subscript form is preferred by users for symmetry with `List[int]`, `Dict[str, int]`, etc., but currently can only be supported via fragile workarounds (string-form `Narrowed[int, 'x > 0']` that exploits forward-reference deferral) or rejected at the API level.

## Why subscript fails today

For `X = Foo[arg1, arg2]`:

1. `SemanticAnalyzer.check_and_set_up_type_alias` decides this is a type alias (heuristic: `Foo` is a class, RHS is a subscript).
2. It calls `expr_to_unanalyzed_type(rvalue)` to translate the RHS into a `Type`.
3. For a `LambdaExpr` argument, no translation rule exists → `TypeTranslationError`.
4. mypy reports `error: Invalid type alias: expression is not a valid type [valid-type]` and the variable `X` is left unregistered as an alias, which then triggers a downstream `error: Variable "X" is not valid as a type [valid-type]` on subsequent uses.

On non-compiled (pure-Python) mypy, plugin authors can monkey-patch `SemanticAnalyzer` methods to intercept, but on compiled mypy these patches are ignored because mypyc emits direct C call sites that don't go through `__dict__` lookup. Compiled mypy is the binary distributed by default, so this is the de-facto blocker for ~all users.

## Existing workarounds and their limitations

1. **Call form `Foo(int, lambda x: x > 0)`** — works via `get_dynamic_class_hook`. But asymmetric with built-in generics; users repeatedly ask why subscript doesn't work.

2. **String form `Foo[int, 'x > 0']`** — strings in type positions are forward references and are deferred to type analysis. A plugin's `get_type_analyze_hook` *may* be able to intercept the parent `Foo` and read `original_str_expr` from the deferred argument before mypy tries to parse it as a forward reference. This is unverified at the time of writing and depends on internal sequencing of mypy's resolution phases.

3. **Instance subscript `factory[int, lambda x: x > 0]`** (where `factory` is a module-level instance, not a class) — the alias-detection heuristic doesn't trigger because the LHS of the subscript is a value, not a class. `expr_to_unanalyzed_type` is never called on the lambda. But mypy then doesn't register the result as a type alias either, since plugin API has no IndexExpr-on-instance hook with `add_symbol_table_node` privileges.

None of these allow `Foo[int, lambda x: x > 0]` directly.

## Proposed API extensions (numbered, with tradeoffs)

### Option 1: Defer non-translatable nodes when fullname has plugin hooks

When `expr_to_unanalyzed_type` encounters a non-translatable AST node (`LambdaExpr`, `ComparisonExpr`, etc.) inside `Subscript(base, args)`:

- Check whether `base` resolves to a fullname that has a registered `get_type_analyze_hook` (and/or a new opt-in marker).
- If yes: wrap the node in a new placeholder type, e.g. `DeferredExpressionType(original_expr=node, line=..., column=...)`, and continue translation.
- If no: raise `TypeTranslationError` as today (no behavior change for non-plugin code).

The plugin's existing `get_type_analyze_hook` for that fullname receives the resulting `UnboundType` with `DeferredExpressionType` args, reads `original_expr`, and decides what to do.

**Pros:**
- Minimal API surface: one new placeholder type, no new hooks.
- Backwards-compatible: only triggers when a plugin is registered for the relevant fullname.
- Reuses the existing forward-reference deferral pattern (strings already defer this way via `original_str_expr`).
- Plugin authors familiar with `get_type_analyze_hook` need only learn that args may now contain raw AST.

**Cons:**
- Plugin code handles raw AST nodes directly — a lower-level interface than typed mypy types.
- Need to thread position info through for correct error reporting.
- Requires deciding which AST node kinds are safe to defer (probably: all non-translatable kinds, gated by plugin-registration).

### Option 2: New plugin hook `get_dynamic_alias_hook`

Mirror `get_dynamic_class_hook` for `IndexExpr`-based assignments:

```python
class Plugin:
    def get_dynamic_alias_hook(self, fullname: str) -> Optional[Callable[[DynamicAliasContext], None]]:
        ...
```

Fires when mypy sees `X = Foo[args]` and `Foo`'s fullname matches. Plugin receives raw AST args (no type-translation attempted), validates them, and registers the alias via `ctx.api.add_symbol_table_node`.

**Pros:**
- Clean API mirror of `get_dynamic_class_hook` — symmetric and easy to teach.
- Explicit opt-in by fullname; no behavior change unless a plugin registers.
- Plugin gets unmolested AST.

**Cons:**
- New hook = new API surface to maintain and document.
- Needs careful interaction with the existing alias-creation path: probably means short-circuiting `expr_to_unanalyzed_type` entirely for the matched fullname.

### Option 3: Plugin-registered AST-to-Type translators

Plugins register translators for specific AST node kinds:

```python
class Plugin:
    def get_ast_translator(
        self,
        node_kind: Type[Expression],
    ) -> Optional[Callable[[Expression, AnalyzeContext], Type]]:
        ...
```

`expr_to_unanalyzed_type` consults registered translators before raising.

**Pros:**
- Most flexible; allows arbitrary syntax extensions, not just lambdas.

**Cons:**
- Significantly larger API surface.
- Security/safety concerns: plugins can effectively rewrite the type system for any AST node.
- Overkill for the lambda use case.

### Option 4: Extend `RawExpressionType` to wrap arbitrary AST

`RawExpressionType` already exists for some literal-related cases. Extend it to wrap any unparseable expression (when in a context where a plugin is registered for the surrounding fullname). Plugin reads via the existing `get_type_analyze_hook`.

**Pros:**
- Smallest change — only modifies one existing type.
- No new hooks.

**Cons:**
- Changes `RawExpressionType` semantics; ripple effects in downstream type analysis (subtype checks, error messages, serialization for incremental cache).
- Less explicit than Option 1.

### Option 5: Soft-fail mode for `expr_to_unanalyzed_type`

Add a flag (or context-sensitive behavior) to `expr_to_unanalyzed_type` that, when invoked from alias-creation context with plugin-registered fullnames in the expression tree, returns a placeholder `Type` for non-translatable nodes instead of raising.

**Pros:**
- Smallest change to existing code paths.
- No new types or hooks.

**Cons:**
- Context-sensitive behavior makes testing harder.
- Easy to accidentally swallow real type errors.
- Implicit and hard to reason about.

## Recommendation

**Option 1** balances minimal API addition with sufficient capability:

- Reuses `get_type_analyze_hook` (no new hook).
- Adds one new placeholder type analogous to existing string-forward-ref handling.
- Strictly opt-in via the existing hook-registration mechanism.
- Backwards-compatible: non-plugin code is unchanged.

**Option 2** is cleaner from an API-symmetry standpoint and may be preferable if explicit opt-in via dedicated hooks is desired. The cost is one additional hook to maintain.

Options 3, 4, and 5 are weaker on the size/implicit-behavior axis.

I am happy to discuss tradeoffs in detail and contribute a PR for the chosen option once direction is agreed.

## Workaround being adopted in the meantime

The library is shipping with two forms:

1. `Narrowed(T, lambda x: ...)` — call form, fully supported.
2. `Narrowed[T, 'x > ...']` — subscript form with string predicate; relies on the (yet-unverified) deferral of `StrExpr` resolution past `get_type_analyze_hook`, validates the string as a predicate AST.

The natural form `Narrowed[T, lambda x: ...]` is rejected at runtime with a message pointing to the call form, and at static-analysis time produces mypy's standard `[valid-type]` error.

## Related

- mypy commit/PR refs (for the alias-detection and `expr_to_unanalyzed_type` paths) — to be linked once issue is filed.
- Library: `narrowing` (the project this issue originates from).
