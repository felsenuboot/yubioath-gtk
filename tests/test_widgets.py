from yubioath_gtk.widgets import format_code


def test_format_code_splits_in_half():
    assert format_code("123456") == "123 456"
    assert format_code("12345678") == "1234 5678"
    assert format_code("1234567") == "123 4567"
