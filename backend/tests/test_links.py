from app.api.links import DEFAULTS
from app.core.db import db_session
from app.db.base import Learner


def test_links_crud_duplicates_workspace_isolation_and_export(client):
    import json

    from app.services.privacy import export_files
    owner = client.post('/workspaces', json={'name': 'Links test'}).json()['id']
    other = client.post('/workspaces', json={'name': 'Other links test'}).json()['id']
    headers = {'X-Lernapp-Workspace': owner}
    try:
        assert client.get('/links', headers=headers).json() == DEFAULTS
        body = {'title': '  Mein Kurs  ', 'url': 'https://example.org/course?x=1&y=2', 'description': 'Zum Üben'}
        result = client.post('/links', headers=headers, json=body)
        assert result.status_code == 201
        saved = result.json()[-1]
        assert saved['title'] == 'Mein Kurs' and saved['url'].endswith('?x=1&y=2')
        assert client.post('/links', headers=headers, json=body).status_code == 409
        body['title'] = 'Mein neuer Kurs'
        assert client.put('/links/' + saved['id'], headers=headers, json=body).json()[-1]['title'] == body['title']
        assert client.get('/links', headers={'X-Lernapp-Workspace': other}).json() == DEFAULTS
        assert client.delete('/links/' + saved['id'], headers={'X-Lernapp-Workspace': other}).status_code == 404
        exported = json.loads(export_files(owner)['learner.json'])
        assert exported['profile']['external_links'][-1]['title'] == body['title']
        for entry in client.get('/links', headers=headers).json():
            assert client.delete('/links/' + entry['id'], headers=headers).status_code == 200
        assert client.get('/links', headers=headers).json() == []
        with db_session() as db:
            assert db.get(Learner, owner).profile['external_links'] == []
    finally:
        with db_session() as db:
            for ident in (owner, other):
                db.delete(db.get(Learner, ident))


def test_invalid_links_are_rejected(client):
    for url in ('javascript:alert(1)', 'file:///tmp/file', 'https://user:secret@example.org', 'not a URL'):
        assert client.post('/links', json={'title': 'Bad link', 'url': url}).status_code == 422
    assert client.post('/links', json={'title': '   ', 'url': 'https://example.org'}).status_code == 422
