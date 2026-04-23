from narrowing import Narrowed


def test_isinstance_narrowed_int():
    assert isinstance(5, Narrowed[int, lambda x: x > 0])
    assert isinstance(1, Narrowed[int, lambda x: x > 0])
    assert not isinstance(0, Narrowed[int, lambda x: x > 0])
    assert not isinstance(-1, Narrowed[int, lambda x: x > 0])
    assert not isinstance('123', Narrowed[int, lambda x: x > 0])
    assert not isinstance('kek', Narrowed[int, lambda x: x > 0])


def test_issubclass_narrowed_int():
    assert issubclass(Narrowed[int, lambda x: x > 0], int)
    assert not issubclass(Narrowed[int, lambda x: x > 0], str)
    assert not issubclass(int, Narrowed[int, lambda x: x > 0])
