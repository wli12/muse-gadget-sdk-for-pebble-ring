import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from make_phone_setup import main


@pytest.mark.parametrize('alias', [False, True])
def test_output_cannot_destroy_webhook_keys(tmp_path, monkeypatch, alias):
    keys = tmp_path / 'webhook.json'
    original = json.dumps({'bearer': 'b' * 64, 'signing_secret': 's' * 64})
    keys.write_text(original)
    keys.chmod(0o600)
    output = keys
    if alias:
        output = tmp_path / 'key-alias.html'
        output.symlink_to(keys)
    monkeypatch.setattr(sys, 'argv', [
        'make_phone_setup.py', '--url', 'https://example.invalid',
        '--keys', str(keys), '--output', str(output),
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert keys.read_text() == original
