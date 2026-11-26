"""Read the fields used by the cluster hover card from a 42 Intra profile."""
from html.parser import HTMLParser
import re


class _ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.name = None
        self.level = None
        self.projects = []
        self.has_projects_section = False
        self.has_profile = False

    def handle_starttag(self, tag, attrs):
        if tag not in ('h2', 'h4', 'div', 'a', 'span'):
            return
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        role = None
        if tag == 'h2' and 'profile-name' in classes:
            self.has_profile = True
            role = 'profile'
        elif tag == 'span' and 'name' in classes and self.inside('profile'):
            role = 'name'
        elif tag == 'a' and 'progress-container' in classes and attrs.get('data-cursus') == '42cursus':
            role = 'cursus'
        elif 'on-progress' in classes and self.inside('cursus'):
            role = 'level'
        elif tag == 'h4' and 'profile-title' in classes:
            role = 'heading'
        elif tag == 'a' and 'project-item' in classes and 'data-cursus' in attrs:
            # Current projects are links. Graded projects live in separate history rows.
            role = 'project'
        self.stack.append((tag, role, []))

    def inside(self, role):
        return any(frame[1] == role for frame in self.stack)

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            self.stack[-1][2].append(data)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            return
        _, role, parts = self.stack.pop()
        if not parts:
            return
        text = ' '.join(''.join(parts).split())
        if role == 'name' and text:
            self.name = text
        elif role == 'level':
            match = re.fullmatch(r'level\s+(\d+)\s*-\s*(\d{1,3})\s*%', text, re.I)
            if match and int(match[2]) <= 100:
                self.level = int(match[1]) + int(match[2]) / 100
        elif role == 'heading' and text.casefold() == 'projects':
            self.has_projects_section = True
        elif role == 'project' and text and text not in self.projects:
            self.projects.append(text)


def parse_profile(body):
    parser = _ProfileParser()
    parser.feed(body.decode())
    parser.close()
    if not parser.has_profile:
        raise ValueError('No student profile found.')
    return {'name': parser.name, 'level': parser.level,
            'projects': parser.projects if parser.has_projects_section or parser.projects else None}
