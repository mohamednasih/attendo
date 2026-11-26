from http.cookiejar import CookieJar
from io import BytesIO
from email.message import Message
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import intra_client
import cluster_layout


class IntraSessionTests(unittest.TestCase):
    def test_clusters_use_meta_session_and_only_return_current_workstations(self):
        active = {'campus_id': 16, 'host': 'c1r2p3', 'login': 'student', 'begin_at': '2026-11-24T09:12:00Z'}
        records = [dict(active, end_at=None, image='photo.jpg'), dict(active, end_at='2026-11-24T10:00:00Z'),
                   dict(active, campus_id=17, host='C1R2P3'), dict(active, host='C1R2P3')]
        opener = Mock()
        opener.open.return_value = BytesIO(json.dumps(records).encode())
        with patch.object(intra_client, 'browser_sessions', return_value=[CookieJar()]) as sessions, \
             patch.object(intra_client, '_cluster_floors', return_value=[]), \
             patch.object(intra_client, 'build_opener', return_value=opener):
            data = intra_client.fetch_clusters()
        sessions.assert_called_once_with('meta.intra.42.fr')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://meta.intra.42.fr/clusters.json')
        self.assertEqual(request.get_header('X-requested-with'), 'XMLHttpRequest')
        self.assertEqual(data['stations'], [active, dict(active, campus_id=17)])
        self.assertTrue(data['fetched_at'])

    def test_empty_cluster_feed_and_invalid_formats(self):
        floor = {'campus_id': 75, 'name': 'Cluster 1', 'seats': [{'host': 'c1r1p1'}], 'labels': []}
        with patch.object(intra_client, '_fetch_json', return_value=[]), \
             patch.object(intra_client, '_cluster_floors', return_value=[floor]):
            data = intra_client.fetch_clusters()
        self.assertEqual(data['stations'], [])
        self.assertEqual(data['floors'], [floor])
        for payload in ({}, [None], [{'host': ''}], [{'host': 'c1r2p3', 'campus_id': []}]):
            with self.subTest(payload=payload), patch.object(intra_client, '_fetch_json', return_value=payload), \
                 self.assertRaises(ValueError):
                intra_client.fetch_clusters()

    def test_official_floor_geometry_preserves_empty_seats_labels_and_offsets(self):
        page = b"""<div id='cluster-map' data-campus-id='75'><div class='map-container' data-floor='Cluster 1'
                   data-image='https://cdn.intra.42.fr/cluster/image/300/E1.svg'></div></div>"""
        self.assertEqual(cluster_layout.floor_index(page),
                         (75, [('Cluster 1', 'https://cdn.intra.42.fr/cluster/image/300/E1.svg')]))
        svg = b"""<svg xmlns="http://www.w3.org/2000/svg"><text x="-120" y="100">R1</text>
                  <g><rect id="c1r1p1"/><image id="c1r1p1" x="-89.75" y="70.25" width="39.5" height="49.5"/>
                  <image id="c1r1p2" x="-34.75" y="90.25" width="39.5" height="49.5"/></g></svg>"""
        plan = cluster_layout.geometry(svg)
        self.assertEqual(plan['labels'], [{'text': 'R1', 'x': -120, 'y': 100}])
        self.assertEqual([(s['host'], s['x'], s['y']) for s in plan['seats']],
                         [('c1r1p1', -89.75, 70.25), ('c1r1p2', -34.75, 90.25)])
        self.assertEqual(plan['seats'][0]['width'], 39.5)

    def test_portraits_only_serve_known_intra_cdn_images(self):
        url = 'https://cdn.intra.42.fr/users/abc123/small_student.JPG'
        records = [{'host': 'c1r1p1', 'cdn_uri': url},
                   {'host': 'c1r1p2', 'cdn_uri': 'https://example.org/users/abc123/photo.jpg'}]
        with patch.object(intra_client, '_fetch_json', return_value=records), \
             patch.object(intra_client, '_cluster_floors', side_effect=ValueError('Unavailable')):
            data = intra_client.fetch_clusters()
        self.assertEqual(data['floors'], [])
        key = data['stations'][0]['portrait'].split('/')[-1]
        self.assertNotIn('portrait', data['stations'][1])
        opener = Mock()
        body = BytesIO(b'photo')
        body.headers = Message()
        body.headers['Content-Type'] = 'image/jpeg'
        opener.open.return_value = body
        with patch.object(intra_client, 'browser_sessions', return_value=[CookieJar()]), \
             patch.object(intra_client, 'build_opener', return_value=opener):
            self.assertIsNone(intra_client.fetch_cluster_portrait('unknown'))
            self.assertEqual(intra_client.fetch_cluster_portrait(key), ('image/jpeg', b'photo'))
        self.assertEqual(opener.open.call_args.args[0].full_url, url)

    def test_cluster_profile_reads_only_cursus_level_and_current_projects(self):
        page = b"""<h2 class="profile-name"><span class="name">Sample &amp; Student</span></h2>
            <a class="progress-container" data-cursus="42cursus"><div class="on-progress">level 10 - 4%</div></a>
            <a class="progress-container" data-cursus="c-piscine"><div class="on-progress">level 9 - 45%</div></a>
            <div class="project-item main-project-item"><a>Finished project</a></div>
            <h4 class="profile-title">Projects<div>Transcript</div></h4>
            <a class="project-item block-item" data-cursus="42cursus">minishell<div>In progress</div></a>
            <a class="project-item block-item" data-cursus="42cursus 42-zip">ft_irc</a>"""
        with patch.object(intra_client, '_cluster_logins', {'student'}), \
             patch.object(intra_client, '_fetch_session', side_effect=lambda *args: args[-1](page)) as fetch:
            self.assertIsNone(intra_client.fetch_cluster_profile('unknown'))
            fetch.assert_not_called()
            self.assertEqual(intra_client.fetch_cluster_profile('student'), {
                'name': 'Sample & Student', 'level': 10.04, 'projects': ['minishell', 'ft_irc']})
            self.assertEqual(fetch.call_args.args[:2], ('profile.intra.42.fr', '/users/student'))

    def test_cluster_profile_distinguishes_missing_fields_and_empty_projects(self):
        header = b'<h2 class="profile-name"></h2>'
        self.assertIsNone(intra_client.parse_profile(header)['projects'])
        profile = intra_client.parse_profile(header + b'<h4 class="profile-title">Projects</h4>')
        self.assertEqual(profile, {'name': None, 'level': None, 'projects': []})
        with self.assertRaises(ValueError):
            intra_client.parse_profile(b'<form>Sign in</form>')

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
