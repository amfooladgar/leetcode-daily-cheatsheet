"""Tests for src/rendering/factory.py -- the single dispatch point between
image_generation providers. See ARCHITECTURE.md "Optional OpenAI image
renderer" for why provider branching must live only here."""

import unittest
from unittest import mock

from src.rendering.base import RenderResult
from src.rendering.factory import (
    UnknownProviderError,
    render_cheatsheet_with_provider,
    validate_provider,
)


class ValidateProviderTests(unittest.TestCase):
    def test_known_providers_do_not_raise(self):
        validate_provider("existing")
        validate_provider("openai")

    def test_unknown_provider_raises(self):
        with self.assertRaises(UnknownProviderError):
            validate_provider("dalle")


class RenderCheatsheetWithProviderTests(unittest.TestCase):
    def _existing_result(self):
        return RenderResult(
            provider="existing", passed=True, width=1080, height=1350, format="PNG", image_path="cheatsheet.png"
        )

    def _openai_result(self):
        return RenderResult(
            provider="openai",
            passed=True,
            width=1536,
            height=1024,
            format="PNG",
            image_path="cheatsheet-openai-final.png",
        )

    def test_unknown_provider_raises_before_any_render_call(self):
        with mock.patch("src.rendering.existing_provider.render") as mock_existing:
            with self.assertRaises(UnknownProviderError):
                render_cheatsheet_with_provider(
                    "dalle",
                    {},
                    {},
                    "img.png",
                    "stage_dir",
                    contact_card_path=None,
                    fallback_to_existing=False,
                )
            mock_existing.assert_not_called()

    def test_existing_provider_dispatches_to_existing_module(self):
        with mock.patch(
            "src.rendering.existing_provider.render", return_value=self._existing_result()
        ) as mock_existing:
            result = render_cheatsheet_with_provider(
                "existing",
                {},
                {},
                "img.png",
                "stage_dir",
                contact_card_path=None,
                fallback_to_existing=False,
            )
        mock_existing.assert_called_once()
        self.assertEqual(result.provider, "existing")

    def test_openai_provider_dispatches_to_openai_module(self):
        with mock.patch(
            "src.rendering.openai_provider.render", return_value=self._openai_result()
        ) as mock_openai, mock.patch("src.rendering.existing_provider.render") as mock_existing:
            result = render_cheatsheet_with_provider(
                "openai",
                {},
                {},
                "img.png",
                "stage_dir",
                contact_card_path=None,
                fallback_to_existing=False,
            )
        mock_openai.assert_called_once()
        mock_existing.assert_not_called()
        self.assertEqual(result.provider, "openai")

    def test_openai_failure_without_fallback_propagates_and_never_calls_existing(self):
        from src.rendering.openai_provider import OpenAIGenerationError

        with mock.patch(
            "src.rendering.openai_provider.render", side_effect=OpenAIGenerationError("boom")
        ), mock.patch("src.rendering.existing_provider.render") as mock_existing:
            with self.assertRaises(OpenAIGenerationError):
                render_cheatsheet_with_provider(
                    "openai",
                    {},
                    {},
                    "img.png",
                    "stage_dir",
                    contact_card_path=None,
                    fallback_to_existing=False,
                )
            mock_existing.assert_not_called()

    def test_openai_failure_with_fallback_enabled_falls_back_to_existing(self):
        from src.rendering.openai_provider import OpenAIGenerationError

        with mock.patch(
            "src.rendering.openai_provider.render", side_effect=OpenAIGenerationError("boom")
        ), mock.patch(
            "src.rendering.existing_provider.render", return_value=self._existing_result()
        ) as mock_existing:
            result = render_cheatsheet_with_provider(
                "openai",
                {},
                {},
                "img.png",
                "stage_dir",
                contact_card_path=None,
                fallback_to_existing=True,
            )
        mock_existing.assert_called_once()
        self.assertEqual(result.provider, "existing")

    def test_fallback_never_triggers_on_existing_provider_failures(self):
        # A failure in the existing renderer itself (e.g. a bad QA result)
        # is returned as a failed RenderResult, not an exception -- fallback
        # logic only ever applies to the openai branch.
        failed = RenderResult(
            provider="existing", passed=False, width=1080, height=1350, format="PNG", image_path="cheatsheet.png"
        )
        with mock.patch("src.rendering.existing_provider.render", return_value=failed) as mock_existing:
            result = render_cheatsheet_with_provider(
                "existing",
                {},
                {},
                "img.png",
                "stage_dir",
                contact_card_path=None,
                fallback_to_existing=True,
            )
        mock_existing.assert_called_once()
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
