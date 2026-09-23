import os
import tempfile
import unittest

from app.config import config
from app.database import get_db, init_db
from app.web.routes_gamification import (
    BuyVipRequest,
    ClaimTaskRequest,
    OpenCaseRequest,
    buy_vip,
    claim_daily,
    game_leaderboard,
    game_profile,
    open_case,
)


class GamificationFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_vip_daily_case_and_season_flow(self):
        telegram_id = 880055
        await game_profile(telegram_id)
        async with get_db() as db:
            await db.execute(
                "UPDATE users SET stars_balance=500, case_keys=10 WHERE telegram_id=?",
                (telegram_id,),
            )
            await db.commit()

        vip = await buy_vip(BuyVipRequest(user_id=telegram_id, payment_method="stars"))
        self.assertTrue(vip["success"])

        daily = await claim_daily(ClaimTaskRequest(user_id=telegram_id))
        self.assertEqual(daily["keys"], 1)  # VIP daily key

        result = await open_case(
            "starter", OpenCaseRequest(user_id=telegram_id, payment_method="key")
        )
        self.assertTrue(result["success"])

        board = await game_leaderboard(telegram_id)
        self.assertGreaterEqual(board["my_points"], 100)


if __name__ == "__main__":
    unittest.main()
