import json
import shlex

from etp.command import Command


def test_simple_roundtrip_with_quotes():
    text = 'set key="value with spaces" path=/tmp/dir "arg with spaces" bare'
    cmd = Command(text)

    assert cmd.name == "set"
    assert cmd.args == ["arg with spaces", "bare"]
    assert cmd.kwargs == {"key": "value with spaces", "path": "/tmp/dir"}

    out = cmd.to_string()
    assert shlex.split(out) == shlex.split(text)


def test_simple_backslash_escapes():
    text = 'set key="a\\\\ b" quote="a\\\"b" path="C:\\\\Temp\\\\File"'
    cmd = Command(text)

    assert cmd.kwargs["key"] == "a\\ b"
    assert cmd.kwargs["quote"] == 'a"b'
    assert cmd.kwargs["path"] == "C:\\Temp\\File"

    out = cmd.to_string()
    roundtrip = Command(out)

    assert roundtrip.kwargs == cmd.kwargs


def test_simple_key_value_only():
    text = 'x=1 y="a b" z=3'
    cmd = Command(text)

    assert cmd.name == ""
    assert cmd.args == []
    assert cmd.kwargs == {"x": "1", "y": "a b", "z": "3"}

    out = cmd.to_string()
    assert shlex.split(out) == shlex.split(text)


def test_sms_format():
    text = "status;temp=12.5;unit=C;alive"
    cmd = Command(text, format="sms")

    assert cmd.name == "status"
    assert cmd.kwargs == {"temp": "12.5", "unit": "C"}
    assert cmd.args == ["alive"]
    assert cmd.to_string("sms") == text


def test_json_format():
    payload = {"name": "set", "args": ["a b", "c"], "kwargs": {"x": "1", "y": 2}, "extra": 1}
    text = json.dumps(payload)
    cmd = Command(text, format="json")

    assert cmd.name == "set"
    assert cmd.args == ["a b", "c"]
    assert cmd.kwargs == {"kwargs": {"x": "1", "y": 2}, "extra": 1}

    out = cmd.to_string("json")
    assert json.loads(out) == payload


def test_create_with_name_args_kwargs():
    cmd = Command.create("set", "a", "b", x="1", y="two")

    assert cmd.name == "set"
    assert cmd.args == ["a", "b"]
    assert cmd.kwargs == {"x": "1", "y": "two"}

    out = cmd.to_string()
    roundtrip = Command(out)

    assert roundtrip.name == cmd.name
    assert roundtrip.args == cmd.args
    assert roundtrip.kwargs == cmd.kwargs


def test_create_without_name():
    cmd = Command.create(x="1", y="two")

    assert cmd.name == ""
    assert cmd.args == []
    assert cmd.kwargs == {"x": "1", "y": "two"}

    out = cmd.to_string()
    roundtrip = Command(out)

    assert roundtrip.name == cmd.name
    assert roundtrip.args == cmd.args
    assert roundtrip.kwargs == cmd.kwargs
