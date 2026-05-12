import re  # noqa: F401  # used by `test_inline_isinstance_subscript_form_predicate_with_stdlib`

import pytest

from narrowing import Narrowed


@pytest.mark.mypy_testing
def test_b_form_simple():
    PositiveInt = Narrowed(int, lambda x: x > 0)
    x: PositiveInt = 5
    reveal_type(x)  # R: builtins.int


@pytest.mark.mypy_testing
def test_b_form_isinstance_narrows():
    PositiveInt = Narrowed(int, lambda x: x > 0)
    value: object = 5
    if isinstance(value, PositiveInt):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_b_form_missing_predicate():
    with pytest.raises(TypeError):
        Bad = Narrowed(int)  # type: ignore[var-annotated, misc]  # E: narrowing: expected Narrowed(T, predicate), got 1 positional argument(s)  [misc]


@pytest.mark.mypy_testing
def test_b_form_string_predicate_pointed_to_subscript():
    with pytest.raises(TypeError):
        Bad = Narrowed(int, 'x > 0')  # type: ignore[var-annotated, misc]  # E: narrowing: string predicate is only valid in subscript form Narrowed[T, "expr"]  [misc]


@pytest.mark.mypy_testing
def test_b_form_lambda_with_two_args():
    with pytest.raises(TypeError):
        Bad = Narrowed(int, lambda x, y: x > y)  # type: ignore[var-annotated, misc]  # E: narrowing: lambda must take exactly one positional argument  [misc]


@pytest.mark.mypy_testing
def test_b_form_def_function_predicate():
    def my_predicate(x: int) -> bool: return x > 0
    with pytest.raises(TypeError):
        Bad = Narrowed(int, my_predicate)  # type: ignore[var-annotated, misc]  # E: narrowing: second argument must be a lambda  [misc]


@pytest.mark.mypy_testing
def test_b_form_undefined_base():
    with pytest.raises(NameError):
        Bad = Narrowed(undefined_class, lambda x: True)  # type: ignore[var-annotated, name-defined, misc]  # E: narrowing: cannot resolve base type 'undefined_class'  [misc]


@pytest.mark.mypy_testing
def test_b_form_literal_base():
    with pytest.raises(TypeError):
        Bad = Narrowed(42, lambda x: True)  # type: ignore[var-annotated, misc]  # E: narrowing: cannot resolve base type 42  [misc]


@pytest.mark.mypy_testing
def test_e_form_simple():
    x: Narrowed[int, 'x > 0'] = 5
    reveal_type(x)  # R: builtins.int


@pytest.mark.mypy_testing
def test_e_form_arity():
    """Type position is required to trigger the plugin's analyze hook."""
    def _annotation_only(x: 'Narrowed[int]') -> None: ...  # E: narrowing: expected Narrowed[T, "expr"], got 1 parameter(s)  [misc]


@pytest.mark.mypy_testing
def test_e_form_extra_args():
    """Three subscript args; using non-string sentinels to avoid forward-ref resolution."""
    def _annotation_only(x: 'Narrowed[int, 42, 99]') -> None: ...  # E: narrowing: expected Narrowed[T, "expr"], got 3 parameter(s)  [misc]


@pytest.mark.mypy_testing
def test_e_form_invalid_syntax():
    def _annotation_only(x: "Narrowed[int, 'x >']") -> None: ...  # E: narrowing: invalid predicate expression  [misc]


@pytest.mark.mypy_testing
def test_e_form_undefined_argument_name():
    def _annotation_only(x: "Narrowed[int, 'value > 0']") -> None: ...  # E: narrowing: predicate body references free name 'value'  [misc]


@pytest.mark.mypy_testing
def test_e_form_non_string_predicate():
    def _annotation_only(x: 'Narrowed[int, 42]') -> None: ...  # E: narrowing: predicate must be a string  [misc]


@pytest.mark.mypy_testing
def test_b_form_literal_assignment_pass():
    PositiveInt = Narrowed(int, lambda x: x > 0)
    x: PositiveInt = 5


@pytest.mark.mypy_testing
def test_e_form_inline_literal_pass():
    x: Narrowed[int, 'x > 0'] = 5


@pytest.mark.mypy_testing
def test_b_form_literal_assignment_fail():
    PositiveInt = Narrowed(int, lambda x: x > 0)
    x: PositiveInt = -5  # E: narrowing: predicate rejected literal -5


@pytest.mark.mypy_testing
def test_e_form_inline_literal_fail():
    x: Narrowed[int, 'x > 0'] = -5  # E: narrowing: predicate rejected literal -5


@pytest.mark.mypy_testing
def test_b_form_non_literal_rvalue_passes():
    """A non-literal rvalue (function call, variable) doesn't trigger narrowing."""
    PositiveInt = Narrowed(int, lambda x: x > 0)
    def make() -> int: return -5
    x: PositiveInt = make()


@pytest.mark.mypy_testing
def test_b_form_multi_lvalue_skips_narrowing():
    """Multi-lvalue assignments are not candidates for predicate narrowing."""
    PositiveInt = Narrowed(int, lambda x: x > 0)
    a = b = 5
    reveal_type(a)  # R: builtins.int


@pytest.mark.mypy_testing
def test_b_form_module_level_alias_resolution():
    """Module-level aliases resolve via the symbol table directly."""
    _ModulePositive = Narrowed(int, lambda x: x > 0)
    x: _ModulePositive = 5


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_narrows_to_int():
    """`isinstance(value, Narrowed(int, ...))` inline narrows to int in the if-block."""
    value: object = 5
    if isinstance(value, Narrowed(int, lambda x: x > 0)):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_narrows_to_str():
    """Same with `str` base + `len(x) > 0` predicate."""
    value: object = 'hello'
    if isinstance(value, Narrowed(str, lambda x: len(x) > 0)):
        reveal_type(value)  # R: builtins.str


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_else_branch_keeps_object():
    """In the else-branch the value is not narrowed."""
    value: object = 5
    if isinstance(value, Narrowed(int, lambda x: x > 0)):
        pass
    else:
        reveal_type(value)  # R: builtins.object


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_via_full_path_import():
    """Same narrowing through the full-path import (`narrowing.narrowed.Narrowed`)."""
    from narrowing.narrowed import Narrowed as NarrowedFull
    value: object = 5
    if isinstance(value, NarrowedFull(int, lambda x: x > 0)):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_with_any_base_falls_back():
    """`Any` base - `_resolve_base_typeinfo` returns None, hook falls back. mypy emits the original arg-type error and value is not narrowed."""
    from typing import Any, cast
    base = cast(Any, int)
    value: object = 5
    if isinstance(value, Narrowed(base, lambda x: True)):  # type: ignore[arg-type, misc]  # E: Argument 2 to "isinstance" has incompatible type "Narrowed[Any]"; expected "_ClassInfo"  [arg-type]
        reveal_type(value)  # R: builtins.object


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_with_optional_base_falls_back():
    """`Optional[int]` is a Union - `_resolve_base_typeinfo` returns None, fallback path; original error stays."""
    from typing import Optional, cast
    base = cast(Optional[int], int)
    value: object = 5
    if isinstance(value, Narrowed(base, lambda x: True)):  # type: ignore[arg-type, misc]  # E: Argument 2 to "isinstance" has incompatible type "Narrowed[Never]"; expected "_ClassInfo"  [arg-type]
        reveal_type(value)  # R: builtins.object


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_with_generic_base_uses_fill_typevars():
    """Generic base `list` - `fill_typevars` produces `list[Any]` for proper narrowing."""
    value: object = [1, 2, 3]
    if isinstance(value, Narrowed(list, lambda items: len(items) > 0)):
        reveal_type(value)  # R: builtins.list[Any]


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_does_not_break_alias_creation():
    """Alias creation alongside inline-isinstance still works."""
    Bounded = Narrowed(int, lambda x: x > 0)
    bounded_value: Bounded = 5
    reveal_type(bounded_value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_alias_then_inline_isinstance_via_alias():
    """Alias → isinstance(value, alias) still narrows correctly."""
    AliasIntPositive = Narrowed(int, lambda x: x > 0)
    value: object = 5
    if isinstance(value, AliasIntPositive):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_alias_inside_function_scope_then_inline_isinstance():
    """Alias defined in function scope works with inline isinstance check."""
    def use_narrowing() -> None:
        InnerScopePositive = Narrowed(int, lambda x: x > 0)
        value: object = 5
        if isinstance(value, InnerScopePositive):
            reveal_type(value)  # R: builtins.int
    use_narrowing()


@pytest.mark.mypy_testing
def test_inline_isinstance_regular_class_still_works():
    """Plain `isinstance(value, int)` continues to narrow normally."""
    value: object = 5
    if isinstance(value, int):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_tuple_classinfo_still_works():
    """`isinstance(value, (int, str))` (tuple second arg) keeps standard semantics."""
    value: object = 5
    if isinstance(value, (int, str)):
        reveal_type(value)  # R: Union[builtins.int, builtins.str]


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_narrows_to_int():
    """`isinstance(value, Narrowed[int, 'x > 0'])` inline narrows to int."""
    value: object = 5
    if isinstance(value, Narrowed[int, 'x > 0']):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_via_full_path_import():
    """Same with the full-path import."""
    from narrowing.narrowed import Narrowed as NarrowedFull
    value: object = 5
    if isinstance(value, NarrowedFull[int, 'x > 0']):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_narrows_to_str():
    value: object = 'hello'
    if isinstance(value, Narrowed[str, 'len(x) > 0']):
        reveal_type(value)  # R: builtins.str


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_else_branch_keeps_object():
    value: object = 5
    if isinstance(value, Narrowed[int, 'x > 0']):
        pass
    else:
        reveal_type(value)  # R: builtins.object


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_literal_pass():
    """Literal that satisfies the predicate produces no error."""
    if isinstance(5, Narrowed(int, lambda x: x > 0)):
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_literal_fail():
    """Literal that violates the predicate triggers a mypy error."""
    if isinstance(-5, Narrowed(int, lambda x: x > 0)):  # E: narrowing: predicate rejected literal -5
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_literal_fail():
    """Same literal-validation works for the subscript form."""
    if isinstance(-5, Narrowed[int, 'x > 0']):  # E: narrowing: predicate rejected literal -5
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_literal_pass():
    """Literal that satisfies the subscript predicate produces no error."""
    if isinstance(5, Narrowed[int, 'x > 0']):
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_predicate_with_stdlib():
    """Subscript predicate referencing imported stdlib module - narrowing works, no error."""
    value: object = 'hello'
    if isinstance(value, Narrowed[str, "re.match(r'.', x) is not None"]):
        reveal_type(value)  # R: builtins.str


@pytest.mark.mypy_testing
def test_inline_isinstance_call_form_predicate_raises_silent():
    """Predicate that raises against the literal - no static error (silent skip in isinstance_hook)."""
    bomb_class = Narrowed(int, lambda x: 1 / x > 0)
    with pytest.raises(ZeroDivisionError):
        isinstance(0, bomb_class)


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_invalid_arity_still_errors():
    """`Narrowed[int]` (one parameter) keeps the existing plugin diagnostic in isinstance position."""
    def _annotation_only(x: 'Narrowed[int]') -> None: ...  # E: narrowing: expected Narrowed[T, "expr"], got 1 parameter(s)  [misc]


@pytest.mark.mypy_testing
def test_inline_isinstance_tuple_with_narrowed_inside():
    """`isinstance(value, (Narrowed(int, ...), str))` - at least the regular `str` element narrows; Narrowed-element should not break."""
    value: object = 'hello'
    if isinstance(value, (Narrowed(int, lambda x: x > 0), str)):
        reveal_type(value)  # R: Union[builtins.int, builtins.str]


@pytest.mark.mypy_testing
def test_inline_isinstance_combined_with_and():
    """Composition: chained `isinstance` with two `Narrowed` clauses - both narrow to int."""
    value: object = 5
    if isinstance(value, Narrowed(int, lambda x: x > 0)) and isinstance(value, Narrowed(int, lambda x: x < 100)):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_negation_in_else():
    """`if not isinstance(...)` keeps else-branch type unchanged (no spurious narrowing)."""
    value: object = 5
    if not isinstance(value, Narrowed(int, lambda x: x > 0)):
        reveal_type(value)  # R: builtins.object


@pytest.mark.mypy_testing
def test_inline_isinstance_with_walrus():
    """Walrus inside isinstance - assigned variable narrows in if-block."""
    def make_value() -> object:
        return 5
    if isinstance(value := make_value(), Narrowed[int, 'x > 0']):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_issubclass_inline_call_form():
    """`issubclass(SomeCls, Narrowed(int, ...))` - analogous to isinstance."""
    class MyInt(int):
        pass
    if issubclass(MyInt, Narrowed(int, lambda x: x > 0)):
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_combined_with_or():
    """`or` composition narrows to the union of both branches' narrowed types."""
    value: object = 5
    if isinstance(value, Narrowed(int, lambda x: x > 0)) or isinstance(value, Narrowed(str, lambda x: len(x) > 0)):  # noqa: SIM101
        reveal_type(value)  # R: Union[builtins.int, builtins.str]


@pytest.mark.mypy_testing
def test_inline_isinstance_with_call_form_predicate_using_stdlib_literal_pass():
    """Call-form lambda referencing stdlib `re` - literal-eval should resolve `re` and not crash."""
    if isinstance('a', Narrowed(str, lambda x: re.match(r'.', x) is not None)):
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_with_call_form_predicate_using_stdlib_literal_fail():
    """Call-form predicate referencing stdlib `re` rejects the literal - error fires."""
    if isinstance('', Narrowed(str, lambda x: re.match(r'.', x) is not None)):  # E: narrowing: predicate rejected literal ''
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_non_literal_first_argument_no_rejection():
    """First arg is a CallExpr (not literal) - literal-rejection must not fire even when predicate would reject."""
    def make_negative() -> int:
        return -5
    if isinstance(make_negative(), Narrowed(int, lambda x: x > 0)):
        pass


@pytest.mark.mypy_testing
def test_inline_isinstance_parenthesised_lambda_predicate():
    """Lambda wrapped in parens parses identically - narrowing must still work."""
    value: object = 5
    if isinstance(value, Narrowed(int, (lambda x: x > 0))):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_with_generic_base_uses_fill_typevars():
    """Generic base in subscript form: `Narrowed[list, "..."]` narrows to `list[Any]`."""
    value: object = [1, 2, 3]
    if isinstance(value, Narrowed[list, 'len(x) > 0']):
        reveal_type(value)  # R: builtins.list[Any]


@pytest.mark.mypy_testing
def test_inline_isinstance_via_builtins_module_attribute():
    """`builtins.isinstance(...)` (callee = MemberExpr) should also be patched, not just the bare name."""
    import builtins
    value: object = 5
    if builtins.isinstance(value, Narrowed(int, lambda x: x > 0)):
        reveal_type(value)  # R: builtins.int


@pytest.mark.mypy_testing
def test_inline_isinstance_subscript_form_with_nested_narrowed_base_falls_back():
    """`Narrowed[Narrowed[int, "x>0"], "x>5"]` - outer base is an IndexExpr (not a TypeInfo),
    so substitution can't compute a base type and falls back to the original mypy error.
    Documents the current behavior: nested Narrowed in subscript form is a known limitation."""
    value: object = 10
    if isinstance(value, Narrowed[Narrowed[int, 'x > 0'], 'x > 5']):  # type: ignore[arg-type]  # E: Argument 2 to "isinstance" has incompatible type "GenericAlias"; expected "_ClassInfo"  [arg-type]
        reveal_type(value)  # R: builtins.object
