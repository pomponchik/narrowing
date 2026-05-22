# Narrow `dis.Instruction.argval` from `Any` to a precise type

## Summary

`dis.Instruction.argval` is currently typed as `Any` in `stdlib/dis.pyi`. Its
runtime value is constrained by the instruction's `opname` and is always one
of a small, well-defined set of types — never anything that warrants `Any`.

Tightening this would help downstream code that introspects bytecode
(linters, refactoring tools, plugin frameworks) keep `--disallow-any-expr` /
`--disallow-any-explicit` clean.

## Why `Any` is too loose, and why `object` is already a strict improvement

A common reaction is "`object` is just as wide as `Any`, what's the point?"
The two differ in a way that matters here:

- `Any` is **bidirectional unsoundness**: every operation on a value typed
  `Any` returns `Any`, infecting callers. `str(instr.argval)` becomes `Any`,
  the `set` we add it to becomes `set[Any]`, and the rest of the function
  carries an `Any` type-trail.
- `object` is **a strict supertype**: only `object`'s own methods are
  available without narrowing. `str(instr.argval)` returns `str`. Code that
  uses `argval` as a specific type must `isinstance` it first.

So replacing `argval: Any` with `argval: object` is already a meaningful
tightening — it stops the `Any` leak even when the consumer doesn't know
the precise opcode-specific type.

## Real-world example

The `narrowing` library uses `dis.get_instructions(code)` to identify free
names referenced by a lambda body and reject those that fall outside its
allowlist:

```python
import dis

free_names: set[str] = set()
for instr in dis.get_instructions(code):
    if instr.opname in ('LOAD_GLOBAL', 'LOAD_NAME', 'LOAD_DEREF', 'LOAD_CLASSDEREF'):
        free_names.add(str(instr.argval))   # ← `argval` is Any here
```

Under `mypy --strict --disallow-any-expr` this single line emits four
diagnostics, none of which point at a real defect:

```
error: Expression type contains "Any" (has type "Iterator[Instruction]")  [misc]
error: Expression type contains "Any" (has type "Instruction")  [misc]
error: Expression type contains "Any" (has type "Instruction")  [misc]
error: Expression has type "Any"  [misc]
```

The library has explicit `# type: ignore[misc]` markers here today purely
because of `argval`'s width.

## Why not just narrow to `str`

That would be wrong. `argval` is the resolved form of `arg`, and the resolution
depends on the opcode:

| Opcode family                                                                                  | Runtime `argval`               |
|------------------------------------------------------------------------------------------------|--------------------------------|
| `LOAD_GLOBAL`, `LOAD_NAME`, `LOAD_DEREF`, `LOAD_CLASSDEREF`, `LOAD_FAST`, `STORE_*`, `DELETE_*`, `LOAD_ATTR`, `IMPORT_NAME`, `IMPORT_FROM`, `LOAD_METHOD` | `str` (a name)                 |
| `LOAD_CONST`                                                                                   | any constant value             |
| `LOAD_CLOSURE`, `MAKE_CELL`                                                                    | `str`                          |
| `JUMP_*`, `POP_JUMP_IF_*`, `FOR_ITER`                                                          | `int` (offset)                 |
| `COMPARE_OP`                                                                                   | `str` (operator)               |
| `BUILD_*`, `UNPACK_*`, `RAISE_VARARGS`                                                         | `int`                          |
| `RESUME`, `PRECALL`, `KW_NAMES`, `LIST_EXTEND`                                                 | `int` or `tuple`               |
| no `arg`                                                                                       | `None`                         |

The `LOAD_CONST` family really is arbitrary — constants can be any of
`int`, `float`, `complex`, `bool`, `str`, `bytes`, `None`, `tuple`,
`frozenset`, `CodeType`, `type`, etc. — so `argval` must accommodate
"truly anything".

## Proposed change

### Minimum-scope option: `Any` → `object`

```python
class _Instruction(NamedTuple):
    ...
    argval: object   # was: Any
    ...
```

This single one-character change is **strict-correct** (`object` is a
supertype of every Python value), eliminates the `Any` leak, and asks
downstream code to narrow before use. It introduces no false constraints.

### Tighter option: discriminated union

```python
argval: str | int | tuple[object, ...] | bytes | object
```

Lists the common cases first so attribute access through narrowing pays off
on the hot path; falls through to `object` for the long tail of `LOAD_CONST`
constants. Equivalent to the minimum-scope option in soundness but somewhat
more informative for IDEs and reveal_type.

### Strictest option (more involved): per-opcode overloads

A `get_instructions` overload set keyed on `Literal[opname]` could give
`str` for the name-loading opcodes and `object` for `LOAD_CONST`. This is
the most precise but requires a non-trivial re-spelling of `Instruction`
or a helper accessor — happy to discuss if there's appetite.

## Recommendation

Start with the minimum-scope option (`object`). It's a one-line change,
unambiguously sound, and removes the `[misc]` noise downstream consumers
hit today. The other two options can layer on later if there's interest.

## Backwards compatibility

`object` is a strict supertype of every runtime value, so any code that
type-checked against `argval: Any` continues to type-check (any read of
`argval` previously produced `Any`, which silently absorbs into anything).
Code that *used* `argval` as if it had a specific type will now be required
to narrow first; that is the intent.

## Suggested PR

Happy to send a PR for the minimum-scope option — three branches in
`stdlib/dis.pyi` (3.10, 3.11–3.12, 3.13+), a couple of lines each.

## Related observations (not part of this proposal)

- `dis._HaveCodeType: TypeAlias = … | Callable[..., Any]` carries `Any`
  legitimately — `dis.dis()` accepts any callable. No change proposed.
- `types.FunctionType.__defaults__: tuple[Any, ...] | None` and
  `types.FunctionType.__globals__: dict[str, Any]` legitimately carry `Any`
  inside their containers (defaults and globals are arbitrary).
