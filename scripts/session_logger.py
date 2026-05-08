#!/usr/bin/env python3
"""
PaperFit Session Logger

Provides observability for PaperFit agent sessions.
Inspired by ECC's observability features (dimension 8).

Features:
- Session-level logging
- Agent decision tracking
- Token usage estimation
- Metrics collection

Usage:
    python session_logger.py start <session_id>
    python session_logger.py log <session_id> <message>
    python session_logger.py track <session_id> <agent> <action>
    python session_logger.py metrics <session_id>
    python session_logger.py export <session_id>
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Session log directory
LOG_DIR = Path(__file__).parent.parent / 'data' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


class SessionLogger:
    """Manages session logging and metrics collection."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.log_file = LOG_DIR / f'{session_id}.jsonl'
        self.meta_file = LOG_DIR / f'{session_id}.meta.json'

    def start(self, user_id: str = 'anonymous', project: str = None):
        """Initialize a new session."""
        meta = {
            'session_id': self.session_id,
            'user_id': user_id,
            'project': project,
            'started_at': datetime.utcnow().isoformat(),
            'ended_at': None,
            'status': 'active',
            'events': [],
            'agents': {},
            'metrics': {
                'total_events': 0,
                'agent_calls': 0,
                'file_edits': 0,
                'compile_runs': 0,
                'visual_defects_found': 0,
                'visual_defects_fixed': 0,
                'iterations': 0
            }
        }

        with open(self.meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2)

        # Write initial log entry
        self._log_event('session_start', {
            'user_id': user_id,
            'project': project
        })

        print(f"✅ Session started: {self.session_id}")
        print(f"   Log file: {self.log_file}")
        print(f"   Meta file: {self.meta_file}")
        return meta

    def _log_event(self, event_type: str, data: dict):
        """Append an event to the session log."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            'data': data
        }

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')

        # Update meta
        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            meta['events'].append(event_type)
            meta['metrics']['total_events'] += 1

            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def log(self, message: str, category: str = 'info'):
        """Log a general message."""
        self._log_event(category, {'message': message})
        print(f"[{category.upper()}] {message}")

    def track_agent(self, agent_name: str, action: str, details: dict = None):
        """Track an agent invocation."""
        event_data = {
            'agent': agent_name,
            'action': action,
            'details': details or {}
        }
        self._log_event('agent_call', event_data)

        # Update agent stats
        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            if agent_name not in meta['agents']:
                meta['agents'][agent_name] = {'calls': 0, 'actions': {}}

            meta['agents'][agent_name]['calls'] += 1
            if action not in meta['agents'][agent_name]['actions']:
                meta['agents'][agent_name]['actions'][action] = 0
            meta['agents'][agent_name]['actions'][action] += 1
            meta['metrics']['agent_calls'] += 1

            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def track_file_edit(self, file_path: str, reason: str):
        """Track a file edit."""
        self._log_event('file_edit', {
            'file': file_path,
            'reason': reason
        })

        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['metrics']['file_edits'] += 1
            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def track_compile(self, success: bool, errors: list = None):
        """Track a compilation run."""
        self._log_event('compile', {
            'success': success,
            'errors': errors or []
        })

        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['metrics']['compile_runs'] += 1
            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def track_defect(self, category: str, page: int, severity: str, fixed: bool = False):
        """Track a visual defect."""
        self._log_event('defect', {
            'category': category,
            'page': page,
            'severity': severity,
            'fixed': fixed
        })

        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            if fixed:
                meta['metrics']['visual_defects_fixed'] += 1
            else:
                meta['metrics']['visual_defects_found'] += 1

            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def track_iteration(self, round_num: int, status: str):
        """Track an iteration round."""
        self._log_event('iteration', {
            'round': round_num,
            'status': status
        })

        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['metrics']['iterations'] += 1
            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

    def end(self, status: str = 'completed'):
        """End the session."""
        self._log_event('session_end', {'status': status})

        if self.meta_file.exists():
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            meta['ended_at'] = datetime.utcnow().isoformat()
            meta['status'] = status

            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

        print(f"✅ Session ended: {self.session_id} ({status})")

    def get_metrics(self) -> dict:
        """Get session metrics."""
        if not self.meta_file.exists():
            return {'error': 'Session not found'}

        with open(self.meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        return {
            'session_id': meta['session_id'],
            'duration': self._calculate_duration(meta['started_at'], meta['ended_at']),
            'status': meta['status'],
            'metrics': meta['metrics'],
            'agents': meta['agents']
        }

    def _calculate_duration(self, start: str, end: Optional[str]) -> str:
        """Calculate session duration."""
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()
            duration = end_dt - start_dt
            return str(duration)
        except Exception:
            return 'unknown'

    def export(self) -> str:
        """Export full session data."""
        if not self.meta_file.exists():
            return json.dumps({'error': 'Session not found'})

        # Load meta
        with open(self.meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        # Load events
        events = []
        if self.log_file.exists():
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    events.append(json.loads(line))

        return json.dumps({
            'meta': meta,
            'events': events,
            'event_count': len(events)
        }, indent=2, default=str)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return datetime.utcnow().strftime('%Y%m%d_%H%M%S')


def main():
    parser = argparse.ArgumentParser(description='PaperFit Session Logger')
    parser.add_argument('command', choices=['start', 'log', 'track', 'metrics', 'export', 'end'])
    parser.add_argument('session_id', nargs='?', help='Session ID')
    parser.add_argument('--user', default='anonymous', help='User ID')
    parser.add_argument('--project', help='Project name')
    parser.add_argument('--message', help='Log message')
    parser.add_argument('--category', default='info', help='Log category')
    parser.add_argument('--agent', help='Agent name')
    parser.add_argument('--action', help='Agent action')
    parser.add_argument('--details', help='Agent action details (JSON)')
    parser.add_argument('--file', help='File path for file edit')
    parser.add_argument('--reason', help='Reason for file edit')
    parser.add_argument('--status', default='completed', help='End status')

    args = parser.parse_args()

    if args.command == 'start':
        session_id = args.session_id or generate_session_id()
        logger = SessionLogger(session_id)
        logger.start(args.user, args.project)

    elif args.command == 'log':
        if not args.session_id:
            print('Error: session_id required')
            sys.exit(1)
        logger = SessionLogger(args.session_id)
        logger.log(args.message or 'No message', args.category)

    elif args.command == 'track':
        if not args.session_id:
            print('Error: session_id required')
            sys.exit(1)
        logger = SessionLogger(args.session_id)
        if args.agent and args.action:
            details = json.loads(args.details) if args.details else None
            logger.track_agent(args.agent, args.action, details)
        else:
            print('Error: --agent and --action required')
            sys.exit(1)

    elif args.command == 'metrics':
        if not args.session_id:
            print('Error: session_id required')
            sys.exit(1)
        logger = SessionLogger(args.session_id)
        metrics = logger.get_metrics()
        print(json.dumps(metrics, indent=2, default=str))

    elif args.command == 'export':
        if not args.session_id:
            print('Error: session_id required')
            sys.exit(1)
        logger = SessionLogger(args.session_id)
        print(logger.export())

    elif args.command == 'end':
        if not args.session_id:
            print('Error: session_id required')
            sys.exit(1)
        logger = SessionLogger(args.session_id)
        logger.end(args.status)


if __name__ == '__main__':
    main()
