import unittest
from unittest.mock import AsyncMock, patch

from app.services.tasks import TaskAvailability, channel_task_availability, is_channel_member


class TaskProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_is_hidden_when_telegram_cannot_find_it(self):
        settings = {
            "task_channel_enabled": "1",
            "task_channel_id": "@removed_channel",
            "task_channel_url": "https://t.me/removed_channel",
        }

        async def setting(key, default=""):
            return settings.get(key, default)

        with patch("app.services.tasks.get_setting", side_effect=setting), patch(
            "app.services.tasks._telegram_request",
            AsyncMock(side_effect=RuntimeError("chat not found")),
        ):
            result = await channel_task_availability()

        self.assertFalse(result.available)

    async def test_channel_is_shown_only_after_live_probe(self):
        async def setting(key, default=""):
            return {
                "task_channel_enabled": "1",
                "task_channel_id": "@real_channel",
                "task_channel_url": "https://t.me/real_channel",
            }.get(key, default)

        with patch("app.services.tasks.get_setting", side_effect=setting), patch(
            "app.services.tasks._telegram_request",
            AsyncMock(return_value={"type": "channel"}),
        ):
            result = await channel_task_availability()

        self.assertEqual(result, TaskAvailability(True))

    async def test_left_user_does_not_pass_membership_check(self):
        with patch("app.services.tasks.get_setting", AsyncMock(return_value="@channel")), patch(
            "app.services.tasks._telegram_request",
            AsyncMock(return_value={"status": "left"}),
        ):
            self.assertFalse(await is_channel_member(123))

    async def test_api_failure_never_approves_membership(self):
        with patch("app.services.tasks.get_setting", AsyncMock(return_value="@channel")), patch(
            "app.services.tasks._telegram_request",
            AsyncMock(side_effect=TimeoutError),
        ):
            with self.assertRaises(TimeoutError):
                await is_channel_member(123)


if __name__ == "__main__":
    unittest.main()
