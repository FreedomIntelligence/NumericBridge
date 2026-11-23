"""Show the community review state of a submission receipt."""

import argparse
import json
from pathlib import Path

from .intake import IntakeClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', help='submission-<sha256> receipt ID')
    parser.add_argument('--intake', required=True, help='community /v1/submissions URL used to submit')
    parser.add_argument('--token-file', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = IntakeClient(args.intake, args.token_file).status(args.receipt)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
