from etp.command import Command
from etp.influx import build_line_protocol


def test_command_influx_roundtrip():
    line = build_line_protocol(
        "weather station",
        tags={"site": "us-west,1", "device id": "rack=1"},
        fields={"temp": 21.5, "count": 2, "status": 'ok "quoted"', "ok": True},
        timestamp=1690000000,
    )
    cmd = Command(line, format="influx")

    assert cmd.name == "weather station"
    assert cmd.kwargs["tags"] == {"device id": "rack=1", "site": "us-west,1"}
    assert cmd.kwargs["fields"]["temp"] == 21.5
    assert cmd.kwargs["fields"]["count"] == 2
    assert cmd.kwargs["fields"]["status"] == 'ok "quoted"'
    assert cmd.kwargs["fields"]["ok"] is True
    assert cmd.kwargs["timestamp"] == 1690000000

    assert cmd.to_string("influx") == line
