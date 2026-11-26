"""Read the floor names and workstation coordinates published on the Intra cluster page."""
from functools import lru_cache
from html.parser import HTMLParser
from math import isfinite
import re
from urllib.request import Request, build_opener
import xml.etree.ElementTree as ET

from attendo_client import _NoRedirect

CDN = 'https://cdn.intra.42.fr'
META = 'https://meta.intra.42.fr'
FLOOR_URL = re.compile(r'https://cdn\.intra\.42\.fr/cluster/image/[0-9]+/[\w%.-]+\.svg', re.ASCII)


class FloorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.campus_id = None
        self.floors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id') == 'cluster-map' and (attrs.get('data-campus-id') or '').isdecimal():
            self.campus_id = int(attrs['data-campus-id'])
        if 'map-container' in attrs.get('class', '').split():
            url = attrs.get('data-image', '')
            if FLOOR_URL.fullmatch(url) and attrs.get('data-floor'):
                self.floors.append((attrs['data-floor'], url))


def floor_index(body):
    page = FloorParser()
    page.feed(body.decode())
    if page.campus_id is None or not page.floors:
        raise ValueError('No campus floor plans found.')
    return page.campus_id, page.floors


def geometry(svg):
    # Copy numeric geometry and plain labels. The dashboard renders its own elements.
    root = ET.fromstring(svg)
    seats = []
    labels = []
    for node in root.iter():
        tag = node.tag.rsplit('}', 1)[-1]
        if tag not in ('text', 'image') or 'x' not in node.attrib or 'y' not in node.attrib:
            continue
        point = {'x': float(node.attrib['x']), 'y': float(node.attrib['y'])}
        if tag == 'text':
            label = ''.join(node.itertext()).strip()
            if label:
                labels.append(dict(point, text=label))
        elif node.get('id'):
            size = {key: float(node.attrib[key]) for key in ('width', 'height')}
            if min(size.values()) <= 0:
                raise ValueError('Invalid seat dimensions.')
            seats.append(dict(point, **size, host=node.attrib['id'].lower()))
    if not seats or not all(isfinite(v) for item in seats + labels for v in item.values() if isinstance(v, float)):
        raise ValueError('Invalid floor coordinates.')
    return {'seats': seats, 'labels': labels}


@lru_cache(maxsize=24)
def fetch_geometry(url, user_agent):
    if not FLOOR_URL.fullmatch(url):
        raise ValueError('Unknown floor plan URL.')
    request = Request(url, headers={'User-Agent': user_agent, 'Referer': META + '/', 'Origin': META})
    with build_opener(_NoRedirect()).open(request, timeout=6) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError('Floor plan is too large.')
    try:
        return geometry(body)
    except (ET.ParseError, KeyError) as error:
        raise ValueError('Invalid floor plan.') from error
