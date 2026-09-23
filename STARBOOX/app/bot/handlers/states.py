from aiogram.fsm.state import State, StatesGroup


class UserFSM(StatesGroup):
    withdraw_amount = State()
    promo_code = State()
    # paid promotion wizard
    campaign_kind = State()
    campaign_audience = State()
    campaign_title = State()
    campaign_target = State()
    campaign_description = State()
    campaign_count = State()
    campaign_confirm = State()
