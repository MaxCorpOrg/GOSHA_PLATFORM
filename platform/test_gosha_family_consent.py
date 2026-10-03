"""Semantic API contract tests with no network or credentials; quality is evaluated separately."""
import json
import unittest
from unittest.mock import patch, MagicMock

from gosha_family_consent import FamilyConsentJudge, ConsentError, request_payload, parse_response


def response(value, status='completed'):
    return {'status': status, 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(value)}]}]}


class ConsentTests(unittest.TestCase):
    draft = {'names': ['Папа'], 'text': 'Я дома'}

    def test_preserves_conversation_model_and_supplies_observed_data(self):
        payload = request_payload('existing-model', {'effort': 'low'}, self.draft,
                                  'Для папы: я дома. Отправить?', 'Давай быстрее, отправь уже')
        self.assertEqual(payload['model'], 'existing-model')
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertFalse(payload['store'])
        self.assertTrue(payload['text']['format']['strict'])
        self.assertNotIn('tools', payload)
        self.assertEqual(json.loads(payload['input']), {'recipients': ['Папа'], 'message': 'Я дома',
            'robot_readback': 'Для папы: я дома. Отправить?', 'new_user_reply': 'Давай быстрее, отправь уже'})

    def test_all_structured_decisions_are_preserved(self):
        for decision in ('send', 'cancel', 'revise', 'clarify'):
            for matches in (True, False):
                expected = {'readback_matches': matches, 'decision': decision}
                self.assertEqual(parse_response(response(expected)), expected)

    def test_rejects_incomplete_refusal_extra_fields_and_non_boolean(self):
        cases = [response({'readback_matches': True, 'decision': 'send'}, 'incomplete'),
                 response({'readback_matches': 'true', 'decision': 'send'}),
                 response({'readback_matches': True, 'decision': 'SEND'}),
                 response({'readback_matches': True, 'decision': 'send', 'enqueue': True}),
                 response({'decision': 'send'}), response('send'),
                 {'status': 'completed', 'output': [{'type': 'message', 'content': [
                     {'type': 'refusal', 'refusal': 'private text'}]}]}]
        for case in cases:
            with self.assertRaises(ConsentError):
                parse_response(case)

    def test_rejects_malformed_and_multiple_outputs(self):
        good = response({'readback_matches': True, 'decision': 'send'})
        multiple = {'status': 'completed', 'output': good['output']*2}
        cases = [None, {}, {'status': 'completed', 'output': None},
                 {'status': 'completed', 'output': ['private text']},
                 {'status': 'completed', 'output': [{'type': 'message', 'content': None}]},
                 {'status': 'completed', 'output': [{'type': 'message', 'content': [
                     {'type': 'output_text', 'text': 'not JSON'}]}]}, multiple]
        for case in cases:
            with self.assertRaises(ConsentError):
                parse_response(case)

    def test_bounds_input_without_sending_or_truncating(self):
        for draft, readback, reply in (({}, 'question', 'yes'), (self.draft, 'x'*6001, 'yes'),
                                     (self.draft, 'question', 'x'*3001), (self.draft, '', ' '),
                                     ({'names': [], 'text': 'body'}, 'question', 'yes')):
            with self.assertRaisesRegex(ConsentError, 'invalid_consent_input'):
                request_payload('model', {}, draft, readback, reply)

    def test_rejects_non_https_and_embedded_credentials(self):
        for url in ('http://api.test/v1', 'https://user:password@api.test/v1',
                    'https://api.test/v1?key=secret', 'https://api.test/v1#fragment'):
            with self.assertRaises(ConsentError):
                FamilyConsentJudge('fake-private-key', 'model', {}, url)

    def connection(self, status=200, raw=None):
        conn = MagicMock()
        conn.getresponse.return_value.status = status
        conn.getresponse.return_value.read.return_value = raw or json.dumps(response(
            {'readback_matches': True, 'decision': 'send'})).encode()
        return conn

    def test_success_uses_bounded_tls_post_and_closes_connection(self):
        conn = self.connection()
        with patch('gosha_family_consent.http.client.HTTPSConnection', return_value=conn) as constructor:
            result = FamilyConsentJudge('fake-private-key', 'model', {}).evaluate(self.draft, 'question', 'yes')
        self.assertEqual(result['decision'], 'send')
        self.assertEqual(constructor.call_args.kwargs['timeout'], 8)
        self.assertIsNotNone(constructor.call_args.kwargs['context'])
        self.assertEqual(conn.request.call_args.args[:2], ('POST', '/v1/responses'))
        self.assertEqual(conn.getresponse.return_value.read.call_args.args, (32769,))
        conn.close.assert_called_once()

    def test_http_failures_and_redirects_never_reveal_response_or_retry(self):
        for status in (302, 401, 429, 500):
            conn = self.connection(status, b'fake-private-key private conversation')
            with patch('gosha_family_consent.http.client.HTTPSConnection', return_value=conn):
                with self.assertRaisesRegex(ConsentError, '^consent_provider_rejected$'):
                    FamilyConsentJudge('fake-private-key', 'model', {}).evaluate(self.draft, 'question', 'yes')
            conn.request.assert_called_once()
            conn.getresponse.return_value.read.assert_not_called()
            conn.close.assert_called_once()

    def test_oversize_invalid_json_and_network_errors_are_safe(self):
        for raw, code in ((b'x'*32769, 'consent_response_too_large'),
                          (b'private malformed data', 'consent_provider_unavailable')):
            conn = self.connection(raw=raw)
            with patch('gosha_family_consent.http.client.HTTPSConnection', return_value=conn):
                with self.assertRaisesRegex(ConsentError, '^'+code+'$'):
                    FamilyConsentJudge('fake-private-key', 'model', {}).evaluate(self.draft, 'question', 'yes')
            conn.close.assert_called_once()
        conn = self.connection()
        conn.request.side_effect = OSError('fake-private-key private data')
        with patch('gosha_family_consent.http.client.HTTPSConnection', return_value=conn):
            with self.assertRaisesRegex(ConsentError, '^consent_provider_unavailable$'):
                FamilyConsentJudge('fake-private-key', 'model', {}).evaluate(self.draft, 'question', 'yes')
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
