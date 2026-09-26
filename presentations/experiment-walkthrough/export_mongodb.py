"""Read-only exp1/exp2 export for the offline presentation.

Run using the original harness environment (pymongo + python-dotenv):
  python export_mongodb.py --env /path/to/harness/.env --out /path/to/experiments.json

Does not execute the harness, invoke a model, create indexes, or write to MongoDB.
Secrets and database connection strings are never included in the export.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True, help='Existing harness .env file')
    parser.add_argument('--out', type=Path, required=True, help='New local JSON output file')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output already exists. Choose a new path to preserve the existing file.')
    from dotenv import dotenv_values
    from pymongo import MongoClient

    env = dotenv_values(args.env)
    uri = env.get('MONGODB_URI')
    if not uri:
        parser.error('The chosen environment file has no MONGODB_URI.')
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, appname='harness-presentation-readonly')
    db = client[env.get('MONGODB_DB') or 'adaptive_harness']
    runs = list(db.runs.find({'experiment_id': {'$in': ['exp1', 'exp2']}}))
    if not runs:
        raise RuntimeError('No matching experiment runs were found.')
    run_ids = [r['_id'] for r in runs]
    events = list(db.events.find({'run_id': {'$in': run_ids}}).sort([('run_id', 1), ('seq', 1)]))
    experiments = list(db.experiments.find({'_id': {'$in': ['exp1', 'exp2']}}))
    evaluations = list(db.evaluations.find({'run_id': {'$in': run_ids}}))
    versions = list(db.harness_versions.find({'_id': {'$in': ['v1', 'v2', 'v3']}}))
    lesson_ids = set()
    for exp in experiments:
        for lesson_id in (exp.get('dmaic', {}).get('improve', {}).get('artifact', {}).get('root_causes', [])):
            lesson_ids.add(lesson_id)
    lessons = list(db.lessons.find({'$or': [
        {'defect.run_id': {'$in': run_ids}}, {'_id': {'$in': sorted(lesson_ids)}}
    ]}, {'embedding': 0}))
    result = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'scope': ['exp1', 'exp2'], 'runs': runs, 'events': events,
        'evaluations': evaluations, 'experiments': experiments,
        'harness_versions': versions, 'lessons': lessons,
    }
    payload = json.dumps(result, ensure_ascii=False, default=str)
    # Stop if a known credential was accidentally captured in a source record.
    for name in ['MONGODB_URI', 'MODEL_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY']:
        secret = env.get(name)
        if secret and len(secret) > 8 and secret in payload:
            raise RuntimeError('A source record contains a credential. Export stopped before writing.')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        handle.write(payload)
    client.close()
    print(f'Exported {len(runs)} runs, {len(events)} events, {len(lessons)} lessons. MongoDB was read only.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Driver exceptions can contain hostnames or connection parameters.
        print(f'Export stopped ({type(error).__name__}). Verify dependencies, connection access, and output path. No database writes were attempted.', file=sys.stderr)
        sys.exit(1)
