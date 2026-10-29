"""A lost settlement response must replay a committed transfer, never debit twice."""
import requests

import main
from bank_agent import runtime
from test_agent_gateway import _headers, _send, gateway  # noqa: F401


def test_response_lost_after_real_core_commit_retries_same_payment_once(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    payer = main.ACCOUNTS['user_zhang']['balance']
    payee = main.ACCOUNTS['user_li']['balance']
    previous = {row['tx_id'] for row in main.TRANSACTIONS}
    pending = _send(client, '给李四转10元', conversation_id='response-lost-after-commit')
    assert pending['approval'] is not None
    underlying = runtime._http
    attempts = []
    def lose_response(method, path, *, state, json_body=None, headers=None):
        response = underlying(method, path, state=state, json_body=json_body, headers=headers)
        if method == 'POST' and 'transfers/execute' in path:
            attempts.append(dict(headers or {}))
            if len(attempts) == 1:
                assert response.status_code == 200
                assert main.ACCOUNTS['user_zhang']['balance'] == payer - 10
                raise requests.ConnectionError('Injected lost response after committed settlement')
        return response
    monkeypatch.setattr(runtime, '_http', lose_response)
    response = client.post('/api/v1/agent/approvals/' + pending['approval']['approval_id'],
                           json={'decision': 'approve'}, headers=_headers())
    assert response.status_code == 200
    result = response.json()
    assert result['business_status'] == 'SUCCEEDED' and result['approval'] is None
    assert len(attempts) == 2 and attempts[0]['Idempotency-Key'] == attempts[1]['Idempotency-Key']
    assert main.ACCOUNTS['user_zhang']['balance'] == payer - 10
    assert main.ACCOUNTS['user_li']['balance'] == payee + 10
    recorded = [row for row in main.TRANSACTIONS if row['tx_id'] not in previous]
    assert len(recorded) == 2 and all(row['amount'] == 10 for row in recorded)
