"""Existing feature tests use the same login endpoint as a real browser."""
from fastapi.testclient import TestClient

from web.app import create_app as application
from web.auth import configure


PASSWORD = 'local-test-password-only'
HEADERS = {'X-THVV-Request': '1'}


def create_app(data_dir, run_jobs=True):
    configure(data_dir, PASSWORD)
    return application(data_dir, run_jobs=run_jobs)


class AuthenticatedClient(TestClient):
    def __enter__(self):
        client = super().__enter__()
        response = client.post('/api/auth/login', headers=HEADERS, json={'username': 'admin', 'password': PASSWORD})
        assert response.status_code == 200, response.text
        return client
