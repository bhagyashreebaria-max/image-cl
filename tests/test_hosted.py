import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.pdf_tools import ToolResult

class HostedTests(unittest.TestCase):
    def test_oversized_hosted_output_returns_actionable_error(self):
        with patch.dict(os.environ, {'VERCEL': '1'}), patch('app.main.run_tool', return_value=ToolResult(b'x' * (4 * 1024 * 1024 + 1), 'large.pdf', input_bytes=1)):
            response = TestClient(app).post('/api/process/compress', files={'files': ('sample.pdf', b'x', 'application/pdf')}, headers={'Origin': 'http://localhost:8000'})
        self.assertEqual(response.status_code, 413)
        self.assertIn('output exceeds', response.json()['detail'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_static_and_api_response_headers(self):
        client = TestClient(app)
        self.assertEqual(client.get('/').status_code, 200)
        self.assertIn("frame-ancestors 'none'", client.get('/').headers['content-security-policy'])
        self.assertEqual(client.get('/api').json()['name'], 'Papertrail PDF Toolkit')
        self.assertEqual(client.post('/api/inspect', headers={'Origin':'https://untrusted.example'}).status_code,403)
