from aiogram.fsm.state import State, StatesGroup

class BuyStarsStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_recipient = State()
    waiting_for_confirmation = State()

class BuyPremiumStates(StatesGroup):
    waiting_for_plan = State()
    waiting_for_recipient = State()
    waiting_for_confirmation = State()

class PromoStates(StatesGroup):
    waiting_for_code = State()

class DepositStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_method = State()

class AdminBroadcastStates(StatesGroup):
    waiting_for_message = State()
    confirm_broadcast = State()

class AdminSettingsStates(StatesGroup):
    waiting_for_stars_markup = State()
    waiting_for_premium_markup = State()
    waiting_for_announcement = State()
    waiting_for_channel_url = State()
    waiting_for_channel_id = State()
    waiting_for_channel_reward = State()

class AdminUserStates(StatesGroup):
    waiting_for_search = State()
    waiting_for_balance_target = State()
    waiting_for_balance_amount = State()

class AdminPromoStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_type = State()
    waiting_for_value = State()
    waiting_for_max_uses = State()
