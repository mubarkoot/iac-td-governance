import json

import pytest

from tdgov.ledger import FileLedger, VerificationError


def test_put_get_history(tmp_path):
    led = FileLedger(tmp_path / "l.jsonl")
    led.put("a", b"1")
    led.put("b", b"x")
    led.put("a", b"2")
    assert led.get("a").value == b"2"
    assert [e.value for e in led.history("a")] == [b"1", b"2"]
    assert [e.key for e in led.scan("")] == ["a", "b"]
    assert led.get("missing") is None
    assert led.verify()


@pytest.mark.parametrize("mutation", ["modify", "delete", "reorder"])
def test_tampering_is_detected(tmp_path, mutation):
    path = tmp_path / "l.jsonl"
    led = FileLedger(path)
    for i in range(4):
        led.put(f"k{i}", f"v{i}".encode())
    lines = path.read_text().splitlines()
    if mutation == "modify":
        e = json.loads(lines[1])
        e["value"] = "dGFtcGVyZWQ="  # base64("tampered")
        lines[1] = json.dumps(e, sort_keys=True)
    elif mutation == "delete":
        del lines[2]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(VerificationError):
        led.verify()
    with pytest.raises(VerificationError):
        led.get("k0")
