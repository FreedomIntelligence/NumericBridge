"""Export public endpoint discovery metadata for the static community site."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .intake import PATH, PUBLIC_PATH, endpoint


def _published_endpoint(url, path, auth):
    parts = urlsplit(endpoint(url))
    if path == PUBLIC_PATH and ('?' in url or '#' in url):
        raise ValueError('Public submission URLs must not include a query or fragment')
    if parts.path != path:
        raise ValueError(f'The published endpoint must use {path}')
    if parts.scheme != 'https' or (parts.port is not None and not 1 <= parts.port <= 65535):
        raise ValueError('The published submission endpoint must use HTTPS')
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        name = parts.hostname.rstrip('.')
        public = '.' in name and not name.endswith(('.localhost', '.local', '.internal', '.lan'))
    else:
        public = ip.is_global and not ip.is_multicast
    if not public:
        raise ValueError('The published submission endpoint must use a public host')
    return {'url': url, 'method': 'POST', 'auth': auth}


def descriptor(url=None, *, public_url=None):
    value = {'schema_version': 1, 'submission': _published_endpoint(url, PATH, 'bearer') if url else None}
    if public_url:
        value['public_submission'] = _published_endpoint(public_url, PUBLIC_PATH, 'none')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        value = descriptor(os.environ.get('NUMERICBRIDGE_INTAKE_URL', '').strip(),
                           public_url=os.environ.get('NUMERICBRIDGE_PUBLIC_INTAKE_URL', '').strip())
    except ValueError:
        parser.error('Use public HTTPS URLs without credentials or queries: NUMERICBRIDGE_INTAKE_URL for '
                     '/v1/submissions and NUMERICBRIDGE_PUBLIC_INTAKE_URL for /v1/public-submissions')
    args.output.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
