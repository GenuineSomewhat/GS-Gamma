import unittest
from unittest.mock import patch

from app import app
from github_controller import fetch_latest_commit, summarize_commit


class GitHubWebhookTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    @patch("github_controller.requests.get")
    def test_fetch_latest_commit_requests_github_api(self, mock_get):
        mock_get.return_value.json.return_value = {
            "sha": "abc123",
            "commit": {"message": "feat: add webhook support"},
            "html_url": "https://github.com/demo/repo/commit/abc123",
        }
        mock_get.return_value.raise_for_status.return_value = None

        with patch.dict("os.environ", {"GITHUB_REPO_OWNER": "demo", "GITHUB_REPO_NAME": "repo"}, clear=False):
            data = fetch_latest_commit()

        self.assertEqual(data["sha"], "abc123")
        self.assertEqual(data["commit"]["message"], "feat: add webhook support")
        mock_get.assert_called_once()

    def test_summarize_commit_builds_payload(self):
        payload = {
            "sha": "deadbeef",
            "commit": {"message": "fix: update commit fetch"},
            "html_url": "https://github.com/demo/repo/commit/deadbeef",
        }

        summary = summarize_commit(payload)

        self.assertEqual(summary["sha"], "deadbeef")
        self.assertEqual(summary["message"], "fix: update commit fetch")
        self.assertIn("github.com", summary["url"])

    @patch("github_controller.fetch_latest_commit")
    def test_github_webhook_returns_commit_payload(self, mock_fetch):
        mock_fetch.return_value = {
            "sha": "123", "commit": {"message": "feat: new release"}, "html_url": "https://github.com/demo/repo/commit/123"
        }

        response = self.client.post(
            "/github/webhook",
            json={"ref": "refs/heads/main", "commits": [{"id": "123"}]},
            headers={"X-GitHub-Event": "push"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["event"], "push")
        self.assertEqual(response.json["commit"]["sha"], "123")


if __name__ == "__main__":
    unittest.main()
