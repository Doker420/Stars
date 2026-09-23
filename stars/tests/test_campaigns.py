import os
import tempfile
import unittest

from app.config import config
from app.database import get_db, init_db
from app.web.routes_api import get_user_by_id_or_tg
from app.web.routes_campaigns import CreateCampaignRequest, available_campaigns, create_campaign


class CampaignTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_db = config.DATABASE_PATH
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        config.DATABASE_PATH = self.db_path
        await init_db()

    async def asyncTearDown(self):
        config.DATABASE_PATH = self.old_db
        os.unlink(self.db_path)

    async def test_premium_poll_campaign_is_only_offered_to_premium_users(self):
        async with get_db() as db:
            owner = await get_user_by_id_or_tg(db, 70001)
            await db.execute("UPDATE users SET stars_balance=1000 WHERE id=?", (owner["id"],))
            await db.commit()

        campaign = await create_campaign(CreateCampaignRequest(
            user_id=70001, task_type="poll_vote", title="Голосование за вариант",
            target_url="https://t.me/example/10", target_count=10,
            premium_only=True, poll_mode="bot",
        ))
        self.assertEqual(campaign["verification_mode"], "poll_answer")
        self.assertEqual(campaign["rate_stars"], 3.75)

        await available_campaigns(70002, 20)  # creates a regular user
        async with get_db() as db:
            premium = await get_user_by_id_or_tg(db, 70003)
            await db.execute("UPDATE users SET telegram_is_premium=1 WHERE id=?", (premium["id"],))
            await db.commit()

        regular_result = await available_campaigns(70002, 20)
        premium_result = await available_campaigns(70003, 20)
        self.assertEqual(regular_result["campaigns"], [])
        self.assertEqual(len(premium_result["campaigns"]), 1)


if __name__ == "__main__":
    unittest.main()
