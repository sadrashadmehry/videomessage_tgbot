"""Read a private VLESS XHTTP link from a file; write an untracked Xray config."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


def convert(link):
    url = urlsplit(link.strip())
    params = parse_qs(url.query)
    if url.scheme != 'vless' or not url.hostname or not url.port:
        raise ValueError('Expected a VLESS link with hostname and port')
    user_id = str(UUID(url.username))
    if params.get('type') != ['xhttp'] or params.get('security') != ['none']:
        raise ValueError('This converter supports encrypted VLESS over XHTTP with security=none')
    encryption = params.get('encryption', [''])[0]
    if not encryption.startswith('mlkem768x25519plus.'):
        raise ValueError('VLESS encryption is required for this non-TLS transport')
    xhttp = json.loads(params.get('extra', ['{}'])[0])
    xhttp.update({name: params[name][0] for name in ('host', 'path', 'mode')})
    return {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'listen': '0.0.0.0', 'port': 1080, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{
            'protocol': 'vless',
            'settings': {'vnext': [{'address': url.hostname, 'port': url.port, 'users': [{'id': user_id, 'encryption': encryption}]}]},
            'streamSettings': {'network': 'xhttp', 'security': 'none', 'xhttpSettings': xhttp},
        }],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('link_file', type=Path)
    parser.add_argument('--output', type=Path, default=Path('secrets/xray.json'))
    args = parser.parse_args()
    config = convert(args.link_file.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(config, output, indent=2)
    print('Private Xray config written; do not commit it.')
