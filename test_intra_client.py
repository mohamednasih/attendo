from http.cookiejar import CookieJar
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import intra_client


class IntraSessionTests(unittest.TestCase):
    def test_empty_location_history_confirms_session_after_a_rejected_profile(self):
        opener = Mock()
        opener.open.side_effect = [HTTPError('', 401, '', {}, None), BytesIO(b'{}')]
        with patch.object(intra_client, 'browser_sessions', return_value=[CookieJar(), CookieJar()]), \
             patch.object(intra_client, 'build_opener', return_value=opener):
            totals, _ = intra_client.fetch_locations('student')
        self.assertEqual(totals, {})
        self.assertEqual(opener.open.call_count, 2)

    def test_missing_and_rejected_sessions_require_sign_in(self):
        for responses in ([], [HTTPError('', 403, '', {}, None)],
                          [intra_client.AttendoSessionError('Redirect')],
                          [BytesIO(b'<form><input type="password"></form>')]):
            opener = Mock()
            opener.open.side_effect = responses
            with self.subTest(responses=responses), \
                 patch.object(intra_client, 'browser_sessions', return_value=[CookieJar() for _ in responses]), \
                 patch.object(intra_client, 'build_opener', return_value=opener), \
                 self.assertRaises(intra_client.IntraSessionError):
                intra_client.fetch_locations('student')

    def test_network_and_data_errors_are_distinct_from_rejected_sessions(self):
        for response in (URLError('offline'), HTTPError('', 500, '', {}, None), BytesIO(b'invalid json')):
            opener = Mock()
            opener.open.side_effect = [response]
            with self.subTest(response=response), \
                 patch.object(intra_client, 'browser_sessions', return_value=[CookieJar()]), \
                 patch.object(intra_client, 'build_opener', return_value=opener), \
                 self.assertRaises(ValueError) as failure:
                intra_client.fetch_locations('student')
            self.assertNotIsInstance(failure.exception, intra_client.IntraSessionError)
