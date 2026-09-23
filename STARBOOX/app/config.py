from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Keys that admins may override at runtime through the ``app_settings`` table.
# Everything else is env-only (tokens, DB URL, provider credentials, referral depth).
RUNTIME_OVERRIDABLE: dict[str, type] = {
    "referral_l1_percent": int,
    "referral_l2_percent": int,
    "referral_l1_bonus": int,
    "referral_l2_bonus": int,
    "min_referral_activity": int,
    "daily_base_reward": int,
    "daily_streak_bonus": int,
    "daily_streak_cap": int,
    "withdraw_min": int,
    "withdraw_max": int,
    "withdraw_cooldown_hours": int,
    "withdraw_min_referrals": int,
    "withdraw_enabled": bool,
    "signup_bonus": int,
    "claim_cooldown_seconds": int,
    "op_cache_sec": int,
    "task_digest_enabled": bool,
    "task_digest_interval_hours": int,
    "task_digest_quiet_from": int,
    "task_digest_quiet_to": int,
    "task_digest_min_tasks": int,
    "task_digest_include_partner": bool,
    "task_digest_rate_per_sec": int,
    "op_intro_enabled": bool,
    "op_intro_sponsors": int,
    "op_intro_grace_sec": int,
    "manual_op_channels": str,
    "support_contact": str,
    "maintenance_mode": bool,
    "maintenance_text": str,
    "broadcast_rate_per_sec": int,
    "notify_referrer": bool,
    "device_check_enabled": bool,
    "device_check_for_withdraw": bool,
    "twink_block_referral": bool,
    "twink_block_withdraw": bool,
    "twink_require_ip_match": bool,
    "twink_ip_window_days": int,
    # partner tasks (API monetization providers)
    "partner_tasks_enabled": bool,
    "partner_reward_channel": int,
    "partner_reward_bot": int,
    "partner_reward_boost": int,
    "partner_reward_resource": int,
    "partner_tasks_limit": int,
    # paid promotion
    "promo_campaigns_enabled": bool,
    "campaign_moderation": bool,
    "campaign_min_target": int,
    "campaign_max_target": int,
    "campaign_reward_channel": int,
    "campaign_reward_bot": int,
    "campaign_reward_post": int,
    "campaign_reward_custom": int,
    "campaign_xtr_per_reward": int,
    "campaign_service_fee_percent": int,
    "campaign_max_active_per_user": int,
}

# (settings field, legacy placeholder from old .env.example, documented endpoint)
LEGACY_PROVIDER_URLS: tuple[tuple[str, str, str], ...] = (
    ("flyer_api_url", "https://api.flyerservice.io", "https://api.flyerhubs.com"),
    ("botohub_api_url", "https://botohub.me/api/v1", "https://botohub.me"),
    ("tgrass_api_url", "https://api.tgrass.online/v1", "https://tgrass.space"),
)

RUNTIME_SETTING_LABELS: dict[str, str] = {
    "referral_l1_percent": "Реф. доля L1, %",
    "referral_l2_percent": "Реф. доля L2, %",
    "referral_l1_bonus": "Бонус за активацию L1, ⭐",
    "referral_l2_bonus": "Бонус за активацию L2, ⭐",
    "min_referral_activity": "Порог активности реферала",
    "daily_base_reward": "Ежедневка: база, ⭐",
    "daily_streak_bonus": "Ежедневка: бонус за день серии, ⭐",
    "daily_streak_cap": "Ежедневка: потолок серии",
    "withdraw_min": "Вывод: минимум, ⭐",
    "withdraw_max": "Вывод: максимум, ⭐ (0 = без лимита)",
    "withdraw_cooldown_hours": "Вывод: кулдаун, ч",
    "withdraw_min_referrals": "Вывод: мин. активных рефералов",
    "withdraw_enabled": "Вывод включён",
    "signup_bonus": "Бонус за регистрацию, ⭐",
    "claim_cooldown_seconds": "Антиспам: пауза между действиями, с",
    "op_cache_sec": "ОП: кэш проверки, с",
    "task_digest_enabled": "Напоминания о заданиях",
    "task_digest_interval_hours": "Напоминания: раз в N часов",
    "task_digest_quiet_from": "Напоминания: тихо с (час)",
    "task_digest_quiet_to": "Напоминания: тихо до (час)",
    "task_digest_min_tasks": "Напоминания: минимум заданий",
    "task_digest_include_partner": "Напоминания: считать партнёрские",
    "task_digest_rate_per_sec": "Напоминания: скорость, сообщений/с",
    "op_intro_enabled": "ОП: мягкий первый вход",
    "op_intro_sponsors": "ОП: спонсоров на первом входе",
    "op_intro_grace_sec": "ОП: длительность мягкого входа, с",
    "manual_op_channels": "ОП: каналы manual (через запятую)",
    "support_contact": "Контакт поддержки (@username)",
    "maintenance_mode": "Режим обслуживания",
    "maintenance_text": "Текст режима обслуживания",
    "broadcast_rate_per_sec": "Рассылка: сообщений в секунду",
    "notify_referrer": "Уведомлять реферера о новых рефералах",
    "device_check_enabled": "Антитвинк: проверка устройства (Mini App)",
    "device_check_for_withdraw": "Антитвинк: вывод только после проверки",
    "twink_block_referral": "Антитвинк: не платить за реферала-твинка",
    "twink_block_withdraw": "Антитвинк: запрет вывода твинкам",
    "twink_require_ip_match": "Антитвинк: считать твинком только при совпадении IP",
    "twink_ip_window_days": "Антитвинк: окно совпадения IP, дней",
    "partner_tasks_enabled": "Партнёрские задания включены",
    "partner_reward_channel": "Партнёр: подписка на канал, ⭐",
    "partner_reward_bot": "Партнёр: запуск бота, ⭐",
    "partner_reward_boost": "Партнёр: буст канала, ⭐",
    "partner_reward_resource": "Партнёр: переход по ссылке, ⭐",
    "partner_tasks_limit": "Партнёр: сколько заданий показывать",
    "promo_campaigns_enabled": "Продвижение за Stars включено",
    "campaign_moderation": "Продвижение: ручная модерация",
    "campaign_min_target": "Продвижение: мин. выполнений",
    "campaign_max_target": "Продвижение: макс. выполнений",
    "campaign_reward_channel": "Продвижение: награда за подписку, ⭐",
    "campaign_reward_bot": "Продвижение: награда за запуск бота, ⭐",
    "campaign_reward_post": "Продвижение: награда за просмотр поста, ⭐",
    "campaign_reward_custom": "Продвижение: награда за своё задание, ⭐",
    "campaign_xtr_per_reward": "Продвижение: XTR за 1 ⭐ награды",
    "campaign_service_fee_percent": "Продвижение: комиссия сервиса, %",
    "campaign_max_active_per_user": "Продвижение: активных кампаний на юзера",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    bot_token: str = "000000000:PLACEHOLDER_TOKEN_REPLACE_ME"
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    database_url: str = "sqlite+aiosqlite:///./data/kodostars.db"

    referral_levels: int = 2
    referral_l1_percent: int = 15
    referral_l2_percent: int = 5
    referral_l1_bonus: int = 10
    referral_l2_bonus: int = 3
    min_referral_activity: int = 2
    notify_referrer: bool = True

    daily_base_reward: int = 5
    daily_streak_bonus: int = 1
    daily_streak_cap: int = 7

    withdraw_min: int = 50
    withdraw_max: int = 0
    withdraw_cooldown_hours: int = 24
    withdraw_min_referrals: int = 0
    withdraw_enabled: bool = True
    signup_bonus: int = 5
    claim_cooldown_seconds: int = 3

    # Web server (Telegram Mini App for device verification). RubyHost exposes the
    # HTTPS address and passes the port to listen on as SERVER_PORT.
    web_public_url: str = ""
    server_port: int = 5050
    web_host: str = "0.0.0.0"
    device_check_enabled: bool = True
    device_check_for_withdraw: bool = True
    twink_block_referral: bool = True
    twink_block_withdraw: bool = False
    twink_require_ip_match: bool = False
    twink_ip_window_days: int = 30

    support_contact: str = ""
    maintenance_mode: bool = False
    maintenance_text: str = "Бот на техническом обслуживании. Загляните чуть позже."
    broadcast_rate_per_sec: int = 20
    throttle_seconds: float = 0.4

    # Upper bound for the Telegram API calls made during startup (get_me,
    # set_my_commands). Without it a blocked network turns boot into a silent hang.
    startup_api_timeout_sec: float = 15.0
    op_timeout_sec: float = 8.0
    op_cache_sec: int = 180
    # Periodic "you have tasks waiting" reminder. The worker wakes up regularly,
    # but a given user is written to at most once per `interval_hours` and never
    # during quiet hours (local-ish: the bot has no per-user timezone, so this is
    # server time — deliberately conservative to avoid night-time pings).
    task_digest_enabled: bool = True
    task_digest_interval_hours: int = 12
    task_digest_quiet_from: int = 23
    task_digest_quiet_to: int = 9
    task_digest_min_tasks: int = 1
    # Count partner offers in the reminder. They are the paid inventory, so this is
    # on by default; it costs one provider round trip per user in the batch.
    task_digest_include_partner: bool = True
    task_digest_rate_per_sec: int = 15

    # Soft onboarding: on the very first /start show at most N sponsors and let the
    # user through for `op_intro_grace_sec`. The full cascade applies afterwards;
    # the sponsors they skipped show up as paid partner tasks in the meantime.
    op_intro_enabled: bool = True
    op_intro_sponsors: int = 2
    op_intro_grace_sec: int = 3600

    # Flyer — https://api.flyerhubs.com/ (key type `sub` → /check, `tasks` → /get_tasks)
    flyer_enabled: bool = True
    flyer_api_key: str = ""
    flyer_api_url: str = "https://api.flyerhubs.com"
    flyer_tasks_limit: int = 5

    subgram_enabled: bool = True
    subgram_api_key: str = ""
    subgram_api_url: str = "https://api.subgram.ru"

    # BotoHub — https://botohub.me/integration (POST /get-tasks-extended, header Auth)
    botohub_enabled: bool = True
    botohub_api_key: str = ""
    botohub_api_url: str = "https://botohub.me"
    botohub_max_op: int = 0

    # PiarFlow — https://piarflow.com/api-docs (POST /sponsors, /sponsors/check, Bearer)
    piarflow_enabled: bool = True
    piarflow_api_key: str = ""
    piarflow_api_url: str = "https://piarflow.com/v1"
    piarflow_max_sponsors: int = 5

    # TGrass — https://tgrass.space/integration (POST /offers, header Auth)
    tgrass_enabled: bool = True
    tgrass_api_key: str = ""
    tgrass_api_url: str = "https://tgrass.space"
    tgrass_offers_limit: int = 0
    tgrass_channels: str = ""

    # Trafsly — https://trafsly.com/api-docs (POST /api/v1/get-sponsors, /confirm-subscription)
    trafsly_enabled: bool = True
    trafsly_api_key: str = ""
    trafsly_api_url: str = "https://api.trafsly.com"
    trafsly_max_sponsors: int = 5

    manual_enabled: bool = True
    manual_op_channels: str = ""

    # --- Partner tasks -------------------------------------------------------------
    # The OP cascade is a gate; the same provider APIs also expose *optional* offers.
    # Those are published as paid tasks: the user gets the fixed rate below for a
    # completion the provider confirms.
    partner_tasks_enabled: bool = True
    partner_tasks_limit: int = 10
    partner_reward_channel: int = 3
    partner_reward_bot: int = 4
    partner_reward_boost: int = 5
    partner_reward_resource: int = 2

    # --- Paid promotion (users buy placement for Telegram Stars) --------------------
    promo_campaigns_enabled: bool = True
    campaign_moderation: bool = True
    campaign_min_target: int = 10
    campaign_max_target: int = 100_000
    campaign_reward_channel: int = 2
    campaign_reward_bot: int = 3
    campaign_reward_post: int = 1
    campaign_reward_custom: int = 2
    # Price: target_count × reward × XTR-per-star × (100 + fee) / 100
    campaign_xtr_per_reward: int = 1
    campaign_service_fee_percent: int = 20
    campaign_max_active_per_user: int = 3

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("referral_levels")
    @classmethod
    def _levels_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("REFERRAL_LEVELS must be >= 1")
        return value

    @field_validator("referral_l1_percent", "referral_l2_percent")
    @classmethod
    def _percent_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("referral percent must be within 0..100")
        return value

    @field_validator(
        "referral_l1_bonus",
        "referral_l2_bonus",
        "min_referral_activity",
        "daily_base_reward",
        "daily_streak_bonus",
        "daily_streak_cap",
        "withdraw_min",
        "withdraw_max",
        "withdraw_cooldown_hours",
        "withdraw_min_referrals",
        "signup_bonus",
        "claim_cooldown_seconds",
        "op_cache_sec",
        "task_digest_interval_hours",
        "task_digest_min_tasks",
        "task_digest_rate_per_sec",
        "op_intro_sponsors",
        "op_intro_grace_sec",
        "partner_tasks_limit",
        "partner_reward_channel",
        "partner_reward_bot",
        "partner_reward_boost",
        "partner_reward_resource",
        "campaign_min_target",
        "campaign_max_target",
        "campaign_reward_channel",
        "campaign_reward_bot",
        "campaign_reward_post",
        "campaign_reward_custom",
        "campaign_xtr_per_reward",
        "campaign_service_fee_percent",
        "campaign_max_active_per_user",
    )
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value must be >= 0")
        return value

    @field_validator("task_digest_quiet_from", "task_digest_quiet_to")
    @classmethod
    def _hour_of_day(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("hour must be within 0..23")
        return value

    @field_validator("broadcast_rate_per_sec")
    @classmethod
    def _rate_range(cls, value: int) -> int:
        if not 1 <= value <= 30:
            raise ValueError("BROADCAST_RATE_PER_SEC must be within 1..30 (Telegram limit)")
        return value

    @model_validator(mode="after")
    def _withdraw_bounds(self) -> "Settings":
        if self.withdraw_max and self.withdraw_max < self.withdraw_min:
            raise ValueError("WITHDRAW_MAX must be 0 or >= WITHDRAW_MIN")
        return self

    @model_validator(mode="after")
    def _upgrade_legacy_provider_urls(self) -> "Settings":
        """Placeholders shipped in earlier .env.example files pointed at hosts that are
        not the documented ones. Map them to the official endpoints transparently."""
        for field, legacy, current in LEGACY_PROVIDER_URLS:
            if getattr(self, field).rstrip("/") == legacy:
                setattr(self, field, current)
        return self

    @property
    def admin_ids(self) -> frozenset[int]:
        if not self.admin_ids_raw.strip():
            return frozenset()
        return frozenset(
            int(part.strip()) for part in self.admin_ids_raw.split(",") if part.strip().lstrip("-").isdigit()
        )

    @property
    def is_placeholder_token(self) -> bool:
        token = self.bot_token.strip()
        return not token or "PLACEHOLDER" in token.upper() or token.startswith("000000000:")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def web_enabled(self) -> bool:
        return self.web_public_url.strip().startswith("https://")

    @property
    def device_check_active(self) -> bool:
        """Device verification needs both the HTTPS Mini App URL and the runtime flag."""
        return self.web_enabled and self.device_check_enabled

    def web_url(self, path: str = "") -> str:
        return f"{self.web_public_url.strip().rstrip('/')}/{path.lstrip('/')}"

    def partner_reward(self, kind: str) -> int:
        """Fixed stars rate for a partner offer of the given kind."""
        mapping = {
            "channel": self.partner_reward_channel,
            "bot": self.partner_reward_bot,
            "boost": self.partner_reward_boost,
            "resource": self.partner_reward_resource,
            "folder": self.partner_reward_resource,
        }
        return max(mapping.get(kind, self.partner_reward_resource), 0)

    def campaign_reward(self, kind: str) -> int:
        mapping = {
            "channel": self.campaign_reward_channel,
            "bot": self.campaign_reward_bot,
            "post": self.campaign_reward_post,
            "custom": self.campaign_reward_custom,
        }
        return max(mapping.get(kind, self.campaign_reward_custom), 0)

    def campaign_price(self, kind: str, target_count: int) -> int:
        """Total XTR the advertiser pays for ``target_count`` completions."""
        reward = self.campaign_reward(kind)
        base = reward * max(target_count, 0) * max(self.campaign_xtr_per_reward, 1)
        with_fee = base * (100 + max(self.campaign_service_fee_percent, 0))
        return max((with_fee + 99) // 100, 1)

    def referral_percent(self, level: int) -> int:
        mapping = {1: self.referral_l1_percent, 2: self.referral_l2_percent}
        return mapping.get(level, 0)

    def referral_bonus(self, level: int) -> int:
        mapping = {1: self.referral_l1_bonus, 2: self.referral_l2_bonus}
        return mapping.get(level, 0)

    def parse_channel_list(self, raw: str) -> list[str]:
        return [part.strip() for part in raw.split(",") if part.strip()]

    def with_overrides(self, overrides: dict[str, Any]) -> "Settings":
        """Return a copy with runtime overrides applied (whitelisted keys only)."""
        clean = {k: v for k, v in overrides.items() if k in RUNTIME_OVERRIDABLE}
        if not clean:
            return self
        return self.model_copy(update=clean)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
