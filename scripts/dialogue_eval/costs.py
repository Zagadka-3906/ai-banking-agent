"""Process-shared evaluation accounting. Contains no bank runtime or secrets."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

NANOS = 1_000_000_000
# Conservative peak-price estimates, not invoices. Legacy aliases are unpriced.
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ (2026-10-28)
DEFAULT_PRICES = {
    'deepseek-flash': {'input_cached': '0.04', 'input_uncached': '2', 'output': '8', 'context_tokens': 1048576},
    'deepseek-v4-pro': {'input_cached': '0.30', 'input_uncached': '9', 'output': '27', 'context_tokens': 1048576},
}


class EvaluationStopped(RuntimeError):
    pass


def positive_money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('INVALID_COST_VALUE') from None
    if not result.is_finite() or not 0 < result <= 1_000_000:
        raise ValueError('INVALID_COST_VALUE')
    return result


def _nanos(value: Decimal) -> int:
    return int((value * NANOS).to_integral_value(rounding=ROUND_CEILING))


def price_profiles(value: Any) -> dict:
    if not isinstance(value, dict) or len(value) > 30:
        raise ValueError('INVALID_PRICE_PROFILES')
    result = {}
    for model, row in value.items():
        if (not isinstance(model, str) or not 1 <= len(model) <= 120 or not isinstance(row, dict)
                or set(row) != {'input_cached', 'input_uncached', 'output', 'context_tokens'}
                or type(row['context_tokens']) is not int or not 1 <= row['context_tokens'] <= 16777216):
            raise ValueError('INVALID_PRICE_PROFILE')
        result[model] = {key: str(positive_money(row[key])) for key in ('input_cached', 'input_uncached', 'output')}
        if Decimal(result[model]['input_cached']) > Decimal(result[model]['input_uncached']):
            raise ValueError('INVALID_CACHE_PRICE')
        result[model]['context_tokens'] = row['context_tokens']
    return result


def token_usage(answer: Any) -> dict[str, int] | None:
    meta = getattr(answer, 'usage_metadata', None) or {}
    raw = (getattr(answer, 'response_metadata', None) or {}).get('token_usage') or {}
    if not isinstance(meta, dict) or not isinstance(raw, dict):
        return None
    incoming = meta.get('input_tokens', raw.get('prompt_tokens'))
    outgoing = meta.get('output_tokens', raw.get('completion_tokens'))
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    details = meta.get('input_token_details') or {}
    raw_details = raw.get('prompt_tokens_details') or {}
    if not isinstance(details, dict) or not isinstance(raw_details, dict):
        return None
    cached = raw.get('prompt_cache_hit_tokens', details.get('cache_read', raw_details.get('cached_tokens', 0)))
    if type(cached) is not int or not 0 <= cached <= incoming:
        return None
    return {'input_tokens': incoming, 'output_tokens': outgoing, 'cache_hit_tokens': cached,
            'cache_miss_tokens': incoming - cached}


class CostLedger:
    """Atomic reservations span workers and model threads, including late SDK exits.

    Currency admission reserves the model's entire configured context plus the
    capped output at uncached prices. Missing/error usage retains that reserve.
    These are configured price estimates; the provider's bill remains authoritative.
    """
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def configure(cls, path: Path, *, max_cost_cny=None, max_calls=2000, max_output_tokens=4096,
                  consecutive_errors=3, prices=None, models=()):
        if (type(max_calls) is not int or not 1 <= max_calls <= 100000
                or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768
                or type(consecutive_errors) is not int or not 1 <= consecutive_errors <= 10):
            raise ValueError('INVALID_EVALUATION_LIMITS')
        rates = price_profiles(DEFAULT_PRICES if prices is None else prices)
        cap = _nanos(positive_money(max_cost_cny)) if max_cost_cny is not None else None
        if cap is not None and any(model not in rates for model in models):
            raise ValueError('COST_CAP_REQUIRES_EXPLICIT_PRICES_FOR_EVERY_MODEL')
        policy = {'max_cost_nanos': cap, 'max_calls': max_calls, 'max_output_tokens': max_output_tokens,
                  'consecutive_errors': consecutive_errors, 'prices': rates, 'currency': 'CNY'}
        ledger = cls(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, policy TEXT, stop TEXT, errors INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, run TEXT, role TEXT, model TEXT, '
                       'status TEXT, accounted_nanos INTEGER, payload TEXT)')
            encoded = json.dumps(policy, sort_keys=True)
            previous = db.execute('SELECT policy FROM control WHERE id=1').fetchone()
            if previous and previous['policy'] != encoded:
                raise ValueError('SHARED_LEDGER_POLICY_MISMATCH')
            db.execute('INSERT OR IGNORE INTO control VALUES (1, ?, NULL, 0)', (encoded,))
        return ledger

    def stop(self, reason: str):
        if reason not in {'WORKER_FAILED', 'WORKER_TIMEOUT', 'SOURCE_CHANGED', 'CONSECUTIVE_TRIAL_ERRORS', 'TASK_FAILED'}:
            raise ValueError('INVALID_STOP_REASON')
        with self.transaction() as db:
            db.execute('UPDATE control SET stop=COALESCE(stop, ?) WHERE id=1', (reason,))

    def reserve(self, run: str, role: str, model: str) -> tuple[str, int]:
        blocked = None
        identifier = uuid.uuid4().hex
        with self.transaction() as db:
            state = db.execute('SELECT * FROM control WHERE id=1').fetchone()
            policy = json.loads(state['policy'])
            count, used = db.execute('SELECT COUNT(*), COALESCE(SUM(accounted_nanos),0) FROM calls').fetchone()
            rate = policy['prices'].get(model)
            reserve = (_nanos((Decimal(rate['input_uncached']) * rate['context_tokens']
                               + Decimal(rate['output']) * policy['max_output_tokens']) / 1_000_000) if rate else None)
            blocked = state['stop'] or ('MODEL_CALL_LIMIT' if count >= policy['max_calls'] else None)
            if not blocked and policy['max_cost_nanos'] is not None:
                if reserve is None:
                    blocked = 'UNPRICED_MODEL'
                elif used + reserve > policy['max_cost_nanos']:
                    blocked = 'ESTIMATED_COST_LIMIT'
            if blocked:
                db.execute('UPDATE control SET stop=COALESCE(stop, ?) WHERE id=1', (blocked,))
            else:
                db.execute('INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (identifier, run, role, model, 'IN_FLIGHT', reserve, '{}'))
        if blocked:
            raise EvaluationStopped(blocked)
        return identifier, policy['max_output_tokens']

    def completed(self, identifier: str, answer: Any):
        usage = token_usage(answer)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM calls WHERE id=?', (identifier,)).fetchone()
            if row is None or row['status'] != 'IN_FLIGHT':
                raise ValueError('INVALID_CALL_COMPLETION')
            policy = json.loads(db.execute('SELECT policy FROM control WHERE id=1').fetchone()[0])
            rate = policy['prices'].get(row['model'])
            amount, stop = row['accounted_nanos'], None
            if usage is None:
                stop = 'MISSING_TOKEN_USAGE'
            elif rate:
                amount = _nanos((Decimal(rate['input_cached']) * usage['cache_hit_tokens']
                                 + Decimal(rate['input_uncached']) * usage['cache_miss_tokens']
                                 + Decimal(rate['output']) * usage['output_tokens']) / 1_000_000)
                if usage['input_tokens'] > rate['context_tokens'] or usage['output_tokens'] > policy['max_output_tokens']:
                    stop = 'USAGE_EXCEEDS_RESERVATION'
            db.execute('UPDATE calls SET status=?, accounted_nanos=?, payload=? WHERE id=?',
                       ('RETURNED' if usage else 'USAGE_UNKNOWN', amount, json.dumps(usage or {}), identifier))
            db.execute('UPDATE control SET errors=0, stop=COALESCE(stop, ?) WHERE id=1', (stop,))

    def failed(self, identifier: str, error: BaseException):
        status = getattr(error, 'status_code', None)
        status = status if type(status) is int and 100 <= status <= 599 else None
        terminal = {400: 'PROVIDER_INVALID_REQUEST', 401: 'PROVIDER_AUTHENTICATION', 402: 'PROVIDER_INSUFFICIENT_BALANCE',
                    403: 'PROVIDER_FORBIDDEN', 404: 'PROVIDER_MODEL_NOT_FOUND', 422: 'PROVIDER_INVALID_PARAMETERS'}
        with self.transaction() as db:
            state = db.execute('SELECT * FROM control WHERE id=1').fetchone()
            errors = state['errors'] + 1
            threshold = json.loads(state['policy'])['consecutive_errors']
            stop = terminal.get(status) or ('CONSECUTIVE_PROVIDER_ERRORS' if errors >= threshold else None)
            # Only errors known to reject the request release its reservation.
            db.execute('UPDATE calls SET status=?, accounted_nanos=CASE WHEN ? THEN 0 ELSE accounted_nanos END, payload=? WHERE id=?',
                       ('PROVIDER_ERROR', status in terminal, json.dumps({'http_status': status}), identifier))
            db.execute('UPDATE control SET errors=?, stop=COALESCE(stop, ?) WHERE id=1', (errors, stop))

    def snapshot(self, run: str | None = None, *, prefix: str | None = None) -> dict:
        with self.transaction() as db:
            state = db.execute('SELECT * FROM control WHERE id=1').fetchone()
            clause, parameters = (' WHERE run=?', (run,)) if run else (' WHERE substr(run,1,?)=?', (len(prefix), prefix)) if prefix else ('', ())
            rows = db.execute('SELECT role, status, accounted_nanos, payload FROM calls' + clause, parameters).fetchall()
            policy = json.loads(state['policy'])
        roles: dict[str, dict] = {}
        for row in rows:
            entry = roles.setdefault(row['role'], {'calls': 0, 'returned': 0, 'input_tokens': 0, 'output_tokens': 0,
                'cache_hit_tokens': 0, 'cache_miss_tokens': 0, 'usage_unknown_calls': 0, 'unpriced_calls': 0,
                'estimated_or_reserved_nanos': 0})
            entry['calls'] += 1
            entry['returned'] += row['status'] in {'RETURNED', 'USAGE_UNKNOWN'}
            entry['usage_unknown_calls'] += row['status'] != 'RETURNED'
            entry['unpriced_calls'] += row['accounted_nanos'] is None
            entry['estimated_or_reserved_nanos'] += row['accounted_nanos'] or 0
            payload = json.loads(row['payload'])
            for key in ('input_tokens', 'output_tokens', 'cache_hit_tokens', 'cache_miss_tokens'):
                entry[key] += payload.get(key, 0)
        for entry in roles.values():
            value = entry.pop('estimated_or_reserved_nanos')
            entry['estimated_or_reserved_cny'] = None if entry['unpriced_calls'] else str(Decimal(value) / NANOS)
        return {'currency': 'CNY', 'pricing_is_estimate': True, 'stop_reason': state['stop'], 'limits': policy, 'roles': roles,
                'calls': sum(row['calls'] for row in roles.values()),
                'estimated_or_reserved_cny': (None if any(row['unpriced_calls'] for row in roles.values()) else
                    str(sum((Decimal(row['estimated_or_reserved_cny']) for row in roles.values()), Decimal(0))))}


class MeteredModel:
    def __init__(self, model, ledger: CostLedger, run: str, role: str, name: str, counters=None):
        self.model, self.ledger, self.run, self.role, self.name = model, ledger, run, role, name
        self.counters = counters

    def bind(self, **kwargs):
        # Format binding must preserve this proxy; an SDK binding alone would
        # bypass reservation, usage reconciliation and the shared stop signal.
        return MeteredModel(self.model.bind(**kwargs), self.ledger, self.run, self.role, self.name, self.counters)

    def invoke(self, messages, **kwargs):
        identifier, cap = self.ledger.reserve(self.run, self.role, self.name)
        count = self.counters.setdefault(self.role, {'calls': 0, 'returned': 0}) if self.counters is not None else None
        if count is not None:
            count['calls'] += 1
        requested_legacy = kwargs.pop('max_tokens', None)
        requested = requested_legacy or kwargs.pop('max_completion_tokens', None) or cap
        kwargs.pop('max_completion_tokens', None)
        if type(requested) is not int or requested <= 0:
            requested = cap
        # ChatOpenAI rewrites max_tokens to max_completion_tokens. DeepSeek's
        # documented wire contract uses max_tokens; extra_body is merged last.
        body = dict(kwargs.get('extra_body') or {})
        body.pop('max_completion_tokens', None)
        body['max_tokens'] = min(requested, cap)
        kwargs['extra_body'] = body
        try:
            answer = self.model.invoke(messages, **kwargs)
        except BaseException as error:
            self.ledger.failed(identifier, error)
            raise
        self.ledger.completed(identifier, answer)
        if count is not None:
            count['returned'] += 1
        return answer


def add_cost_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--max-cost-cny', type=positive_money)
    group.add_argument('--unlimited-cost', action='store_true', help='Explicitly authorize no currency limit')
    parser.add_argument('--max-model-calls', type=int, default=2000)
    parser.add_argument('--max-output-tokens', type=int, default=4096)
    parser.add_argument('--price-file', type=Path, help='CNY per million tokens; required for unknown model currency caps')
    parser.add_argument('--budget-ledger', type=Path, help='Shared ignored SQLite ledger for a whole evaluation campaign')
    parser.add_argument('--eval-env-file', type=Path, help='Optional separate evaluation credentials; never copied into reports')
    parser.add_argument('--stop-on-failure', action='store_true', help='Stop a diagnostic campaign on the first failed task')


def evaluation_values(root: Path, env_file: Path | None = None) -> dict:
    import os
    from dotenv import dotenv_values
    values = {**dotenv_values(root/'.env'), **os.environ}
    if env_file:
        if not env_file.is_file():
            raise ValueError('EVALUATION_ENV_FILE_NOT_FOUND')
        values.update({key: value for key, value in dotenv_values(env_file).items() if value is not None})
    # Agent has its own override; user/judge retain their explicit overrides.
    for field in ('API_KEY', 'BASE_URL'):
        if values.get('BANK_EVAL_AGENT_'+field):
            values['DEEPSEEK_'+field] = values['BANK_EVAL_AGENT_'+field]
    if values.get('BANK_EVAL_AGENT_MODEL'):
        values['BANK_LLM_MODEL'] = values['BANK_EVAL_AGENT_MODEL']
    return values


def configure_budget(args, root: Path, output: Path, models) -> CostLedger:
    if args.max_cost_cny is None and not args.unlimited_cost:
        raise ValueError('Choose --max-cost-cny or explicitly --unlimited-cost')
    path = (args.budget_ledger or output/'budget.sqlite3').resolve()
    if not path.is_relative_to(root/'build') or path == root/'build':
        raise ValueError('Budget ledger must stay in ignored build/')
    prices = json.loads(args.price_file.read_text(encoding='utf-8')) if args.price_file else None
    return CostLedger.configure(path, max_cost_cny=args.max_cost_cny, max_calls=args.max_model_calls,
        max_output_tokens=args.max_output_tokens, prices=prices, models=models)


def nested_cost_arguments(args, ledger: CostLedger) -> list[str]:
    result = ['--budget-ledger', str(ledger.path), '--max-model-calls', str(args.max_model_calls),
              '--max-output-tokens', str(args.max_output_tokens)]
    result += ['--max-cost-cny', str(args.max_cost_cny)] if args.max_cost_cny is not None else ['--unlimited-cost']
    if args.price_file:
        result += ['--price-file', str(args.price_file.resolve())]
    if args.eval_env_file:
        result += ['--eval-env-file', str(args.eval_env_file.resolve())]
    if getattr(args, 'stop_on_failure', False):
        result += ['--stop-on-failure']
    return result
