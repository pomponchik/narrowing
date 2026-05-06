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
    def _annotation_only(x: "Narrowed[int, 'x >']") -> None: ...  # E: narrowing: invalid predicate expression: invalid syntax  [misc]


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
