from etp.influx import build_line_protocol


def test_build_line_protocol_basic():
    line = build_line_protocol(
        "weather",
        tags={"b": "2", "a": "1"},
        fields={"temp": 82.5, "count": 3, "ok": True},
        timestamp=123,
    )

    assert line == "weather,a=1,b=2 count=3i,ok=true,temp=82.5 123"


def test_build_line_protocol_escapes():
    line = build_line_protocol(
        "weather station,1",
        tags={"device id": "rack=1,slot 2"},
        fields={"path": r"C:\Temp", "status": 'ok "quoted"'},
        timestamp=42,
    )

    assert (
        line
        == 'weather\\ station\\,1,device\\ id=rack\\=1\\,slot\\ 2 '
        'path="C:\\\\Temp",status="ok \\"quoted\\"" 42'
    )
