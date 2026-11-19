"""Read-only diagnostic for a proposed packet; never dispatches an agent."""
import argparse
import json

from .policy import assess_dispatch, load_json, load_policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--policy')
    args = parser.parse_args(argv)
    try:
        result = assess_dispatch(load_json(args.proposal), load_json(args.ledger), load_policy(args.policy))
    except (OSError, ValueError) as error:
        print(json.dumps({'allowed': False, 'dispatch_state': 'not_dispatched',
                          'error': 'invalid_input', 'message': str(error)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['allowed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
