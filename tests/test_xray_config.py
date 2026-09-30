import pytest
from scripts.configure_xray import convert


def test_xhttp_settings_are_preserved_and_plaintext_is_rejected():
    link = 'vless://00000000-0000-0000-0000-000000000001@example.com:443?type=xhttp&security=none&encryption=mlkem768x25519plus.random.0rtt.TEST&host=example.org&path=%2Fassets&mode=stream-up&extra=%7B%22xPaddingBytes%22%3A%22100-1000%22%7D'
    config = convert(link)
    outbound = config['outbounds'][0]
    assert outbound['settings']['vnext'][0]['users'][0]['encryption'].endswith('.TEST')
    assert outbound['streamSettings']['xhttpSettings'] == {'host': 'example.org', 'path': '/assets', 'mode': 'stream-up', 'xPaddingBytes': '100-1000'}
    with pytest.raises(ValueError):
        convert(link.replace('mlkem768x25519plus.random.0rtt.TEST', 'none'))
