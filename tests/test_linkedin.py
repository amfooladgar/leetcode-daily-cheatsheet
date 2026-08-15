"""Unit tests for src/storage/linkedin.py, following google_drive.py's /
telegram.py's mocked-HTTP testing pattern (unittest.mock.patch on the HTTP
call, no real network -- see CLAUDE.md's testing rule)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.storage.linkedin import LinkedInPostError, _truncate_caption, post_cheatsheet


def _fake_png(tmp: Path) -> Path:
    path = tmp / "cheatsheet.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    return path


def _init_upload_response(status_code: int = 200) -> mock.Mock:
    resp = mock.Mock(status_code=status_code)
    resp.json.return_value = {
        "value": {
            "uploadUrl": "https://upload.linkedin.com/fake-upload-url",
            "image": "urn:li:image:fake-image-urn",
        }
    }
    resp.text = json.dumps(resp.json.return_value)
    return resp


def _create_post_response(
    status_code: int = 201, post_urn: str = "urn:li:share:12345"
) -> mock.Mock:
    resp = mock.Mock(status_code=status_code)
    resp.headers = {"x-restli-id": post_urn} if status_code == 201 else {}
    resp.json.return_value = {"message": "something went wrong"}
    resp.text = json.dumps(resp.json.return_value)
    return resp


class LinkedInPostTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "LINKEDIN_ACCESS_TOKEN": "fake-access-token",
                "LINKEDIN_PERSON_URN": "urn:li:person:fake123",
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_missing_credentials_raises(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(LinkedInPostError) as ctx,
        ):
            post_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("LINKEDIN_ACCESS_TOKEN", str(ctx.exception))
        self.assertIn("LINKEDIN_PERSON_URN", str(ctx.exception))

    def test_successful_post_returns_result_and_sends_right_body(self):
        image_path = _fake_png(self.tmpdir)
        init_resp = _init_upload_response()
        put_resp = mock.Mock(status_code=201, text="")
        post_resp = _create_post_response()

        with (
            mock.patch("requests.post", side_effect=[init_resp, post_resp]) as mock_post,
            mock.patch("requests.put", return_value=put_resp) as mock_put,
        ):
            result = post_cheatsheet(
                image_path=image_path,
                caption="Never Forget This Trick",
                visibility="PUBLIC",
                api_version="202608",
            )

        self.assertEqual(result.post_urn, "urn:li:share:12345")
        self.assertEqual(
            result.post_url, "https://www.linkedin.com/feed/update/urn:li:share:12345/"
        )

        # Call 1: initializeUpload
        init_call = mock_post.call_args_list[0]
        self.assertIn("images?action=initializeUpload", init_call.args[0])
        self.assertEqual(
            init_call.kwargs["json"]["initializeUploadRequest"]["owner"], "urn:li:person:fake123"
        )
        self.assertEqual(init_call.kwargs["headers"]["Authorization"], "Bearer fake-access-token")
        self.assertEqual(init_call.kwargs["headers"]["LinkedIn-Version"], "202608")

        # Image PUT
        put_call = mock_put.call_args
        self.assertEqual(put_call.args[0], "https://upload.linkedin.com/fake-upload-url")
        self.assertEqual(put_call.kwargs["headers"], {"Authorization": "Bearer fake-access-token"})
        self.assertEqual(put_call.kwargs["data"], image_path.read_bytes())

        # Call 2: create post
        create_call = mock_post.call_args_list[1]
        self.assertIn("/rest/posts", create_call.args[0])
        body = create_call.kwargs["json"]
        self.assertEqual(body["author"], "urn:li:person:fake123")
        self.assertEqual(body["commentary"], "Never Forget This Trick")
        self.assertEqual(body["visibility"], "PUBLIC")
        self.assertEqual(body["content"]["media"]["id"], "urn:li:image:fake-image-urn")
        self.assertEqual(body["lifecycleState"], "PUBLISHED")

    def test_init_upload_non_200_raises(self):
        init_resp = _init_upload_response(status_code=401)
        init_resp.text = '{"message": "invalid token"}'

        with (
            mock.patch("requests.post", return_value=init_resp),
            self.assertRaises(LinkedInPostError) as ctx,
        ):
            post_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("invalid token", str(ctx.exception))

    def test_image_put_non_2xx_raises(self):
        init_resp = _init_upload_response()
        put_resp = mock.Mock(status_code=500, text="upload server error")

        with (
            mock.patch("requests.post", return_value=init_resp),
            mock.patch("requests.put", return_value=put_resp),
            self.assertRaises(LinkedInPostError) as ctx,
        ):
            post_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("upload server error", str(ctx.exception))

    def test_create_post_non_201_raises_with_api_message(self):
        init_resp = _init_upload_response()
        put_resp = mock.Mock(status_code=201, text="")
        post_resp = _create_post_response(status_code=422)
        post_resp.json.return_value = {"message": "duplicate post detected"}
        post_resp.text = json.dumps(post_resp.json.return_value)

        with (
            mock.patch("requests.post", side_effect=[init_resp, post_resp]),
            mock.patch("requests.put", return_value=put_resp),
            self.assertRaises(LinkedInPostError) as ctx,
        ):
            post_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("duplicate post detected", str(ctx.exception))

    def test_caption_is_truncated_to_3000_chars(self):
        long_caption = "x" * 5000
        self.assertEqual(len(_truncate_caption(long_caption)), 3000)
        self.assertTrue(_truncate_caption(long_caption).endswith("..."))

        init_resp = _init_upload_response()
        put_resp = mock.Mock(status_code=201, text="")
        post_resp = _create_post_response()

        with (
            mock.patch("requests.post", side_effect=[init_resp, post_resp]) as mock_post,
            mock.patch("requests.put", return_value=put_resp),
        ):
            post_cheatsheet(image_path=_fake_png(self.tmpdir), caption=long_caption)

        sent_commentary = mock_post.call_args_list[1].kwargs["json"]["commentary"]
        self.assertEqual(len(sent_commentary), 3000)


if __name__ == "__main__":
    unittest.main()
