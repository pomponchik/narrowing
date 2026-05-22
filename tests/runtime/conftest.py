import pytest

from narrowing import Narrowed


@pytest.fixture(params=['call_form', 'subscript_form'])
def make_narrowed(request):
    """
    Parametrize a test across both surface forms (call and subscript).

    The form-specific builders are defined inside the fixture to keep them
    out of the module-level test namespace; only `make_narrowed` is exposed
    to tests.
    """
    def make_call_form(base, source):
        """Build a Narrowed via call-form, evaluating `source` as the lambda body."""
        return Narrowed(base, eval(f'lambda x: {source}'))

    def make_subscript_form(base, source):
        """Build a Narrowed via subscript-form with `source` as the predicate string."""
        return Narrowed[base, source]

    if request.param == 'call_form':
        return make_call_form
    return make_subscript_form
