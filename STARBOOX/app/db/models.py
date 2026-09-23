from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LedgerKind(StrEnum):
    SIGNUP = "signup"
    DAILY = "daily"
    TASK = "task"
    BOOST_PACK = "boost_pack"
    REFERRAL_BONUS = "referral_bonus"
    REFERRAL_SHARE = "referral_share"
    WITHDRAW_HOLD = "withdraw_hold"
    WITHDRAW_REFUND = "withdraw_refund"
    WITHDRAW_SENT = "withdraw_sent"
    ADMIN_ADJUST = "admin_adjust"
    PROMO = "promo"
    REFUND_REVOKE = "refund_revoke"
    PARTNER_TASK = "partner_task"
    PROMO_TASK = "promo_task"


LEDGER_KIND_LABELS: dict[str, str] = {
    LedgerKind.SIGNUP.value: "Бонус за регистрацию",
    LedgerKind.DAILY.value: "Ежедневная награда",
    LedgerKind.TASK.value: "Задание",
    LedgerKind.BOOST_PACK.value: "Покупка пака",
    LedgerKind.REFERRAL_BONUS.value: "Бонус за реферала",
    LedgerKind.REFERRAL_SHARE.value: "Доля с реферала",
    LedgerKind.WITHDRAW_HOLD.value: "Заявка на вывод",
    LedgerKind.WITHDRAW_REFUND.value: "Возврат по заявке",
    LedgerKind.WITHDRAW_SENT.value: "Выплата",
    LedgerKind.ADMIN_ADJUST.value: "Корректировка админа",
    LedgerKind.PROMO.value: "Промокод",
    LedgerKind.REFUND_REVOKE.value: "Возврат платежа",
    LedgerKind.PARTNER_TASK.value: "Задание партнёра",
    LedgerKind.PROMO_TASK.value: "Задание пользователя",
}


class WithdrawalStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    APPROVED_MANUAL = "approved_manual"
    SENT = "sent"


OPEN_WITHDRAWAL_STATUSES: tuple[str, ...] = (
    WithdrawalStatus.PENDING.value,
    WithdrawalStatus.APPROVED_MANUAL.value,
)

WITHDRAWAL_STATUS_LABELS: dict[str, str] = {
    WithdrawalStatus.PENDING.value: "ожидает",
    WithdrawalStatus.REJECTED.value: "отклонена",
    WithdrawalStatus.CANCELLED.value: "отменена",
    WithdrawalStatus.APPROVED_MANUAL.value: "согласована",
    WithdrawalStatus.SENT.value: "выплачена",
}


class BoostKind(StrEnum):
    MULTIPLIER = "multiplier"
    STARS_PACK = "stars_pack"


class TaskKind(StrEnum):
    SUBSCRIBE = "subscribe"
    INVITE = "invite"
    STREAK = "streak"
    CUSTOM = "custom"


TASK_KIND_LABELS: dict[str, str] = {
    TaskKind.SUBSCRIBE.value: "Подписка на канал",
    TaskKind.INVITE.value: "Пригласить друзей",
    TaskKind.STREAK.value: "Серия ежедневок",
    TaskKind.CUSTOM.value: "Перейти по ссылке",
}


class PaymentStatus(StrEnum):
    PAID = "paid"
    REFUNDED = "refunded"


class BroadcastStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BroadcastAudience(StrEnum):
    ALL = "all"
    ACTIVE_7D = "active_7d"
    ACTIVATED = "activated"


BROADCAST_AUDIENCE_LABELS: dict[str, str] = {
    BroadcastAudience.ALL.value: "Все пользователи",
    BroadcastAudience.ACTIVE_7D.value: "Активные за 7 дней",
    BroadcastAudience.ACTIVATED.value: "С активированной рефкой",
}


class AdminRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_last_action_at", "last_action_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str] = mapped_column(String(8), default="ru")
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    referred_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    referral_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    activity_score: Mapped[int] = mapped_column(Integer, default=0)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_daily_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_withdraw_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Any interaction with the bot (touched on every update) — powers DAU/WAU.
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last *rewarded* action (daily, task, promo, partner task, campaign). The
    # anti-spam cooldown is measured against this one, never against last_action_at:
    # the latter is refreshed by the middleware on every tap and would block forever.
    last_claim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_op_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Start of the soft-onboarding window: the first /start shows only a couple of
    # sponsors and lets the user in, so they can see the bot before grinding the
    # whole cascade. The rest of the sponsors become rewarded partner tasks.
    op_intro_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_bot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Periodic "new tasks are waiting" reminder: opt-out flag and the last send,
    # so a restart cannot make the bot spam everyone again.
    task_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_task_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Anti-multiaccount (device verification through the Mini App).
    device_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_fp: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    twink_of: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ledger_entries: Mapped[list[LedgerEntry]] = relationship(back_populates="user")
    withdrawals: Mapped[list[Withdrawal]] = relationship(back_populates="user")

    @property
    def is_twink(self) -> bool:
        """Flagged as a multi-account and not whitelisted by an admin."""
        return self.twink_of is not None and not self.is_trusted

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.id)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_user_created", "user_id", "created_at"),
        Index("ix_ledger_kind_ref", "kind", "reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="ledger_entries")


class ReferralEdge(Base):
    __tablename__ = "referral_edges"
    __table_args__ = (
        UniqueConstraint("referrer_id", "referee_id", "level", name="uq_referral_edge"),
        Index("ix_referral_referrer", "referrer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    referee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    level: Mapped[int] = mapped_column(Integer)
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyClaim(Base):
    __tablename__ = "daily_claims"
    __table_args__ = (UniqueConstraint("user_id", "claimed_on", name="uq_daily_claim"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    claimed_on: Mapped[date] = mapped_column(Date)
    streak: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32))
    reward: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class UserTask(Base):
    __tablename__ = "user_tasks"
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_user_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BoostProduct(Base):
    __tablename__ = "boost_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    xtr_price: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    multiplier_bp: Mapped[int] = mapped_column(Integer, default=100)
    duration_hours: Mapped[int] = mapped_column(Integer, default=0)
    stars_amount: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserBoost(Base):
    __tablename__ = "user_boosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("boost_products.id"))
    multiplier_bp: Mapped[int] = mapped_column(Integer, default=100)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    """Money record for every Telegram Stars purchase (entitlement lives in UserBoost)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("boost_products.id"), nullable=True)
    telegram_charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    provider_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_payload: Mapped[str | None] = mapped_column(String(128), nullable=True)
    xtr_amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PAID.value)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default=WithdrawalStatus.PENDING.value)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="withdrawals")


class ProviderState(Base):
    __tablename__ = "provider_states"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FraudEvent(Base):
    __tablename__ = "fraud_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceCheck(Base):
    """One Mini App verification: who, from which device fingerprint and IP."""

    __tablename__ = "device_checks"
    __table_args__ = (Index("ix_device_checks_ip_created", "ip", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    fp_hash: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tg_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    matched_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Admin(Base):
    """Admins managed from the bot. Env ``ADMIN_IDS`` are owners and never stored here."""

    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default=AdminRole.ADMIN.value)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (Index("ix_admin_actions_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(48))
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    from_chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    button_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    button_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audience: Mapped[str] = mapped_column(String(32), default=BroadcastAudience.ALL.value)
    status: Mapped[str] = mapped_column(String(16), default=BroadcastStatus.PENDING.value)
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    reward: Mapped[int] = mapped_column(Integer)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"
    __table_args__ = (UniqueConstraint("promo_id", "user_id", name="uq_promo_redemption"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(Integer, ForeignKey("promo_codes.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- partner tasks (monetization providers) ------------------------------------------


class PartnerTaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    EXPIRED = "expired"


class PartnerTaskClaim(Base):
    """One partner (Flyer/SubGram/…) task taken by a user.

    Provider APIs are stateless from our side: the offer list is fetched per user on
    every screen. This table is the local journal — it makes the reward idempotent
    (``uq_partner_claim``) and lets admins see what was paid for which offer.
    """

    __tablename__ = "partner_task_claims"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_id", name="uq_partner_claim"),
        Index("ix_partner_claims_provider_created", "provider", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="channel")
    title: Mapped[str] = mapped_column(String(160), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    reward: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=PartnerTaskStatus.PENDING.value)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- paid promotion (users buy placement for Stars) ----------------------------------


class CampaignKind(StrEnum):
    CHANNEL = "channel"
    BOT = "bot"
    POST = "post"
    CUSTOM = "custom"


CAMPAIGN_KIND_LABELS: dict[str, str] = {
    CampaignKind.CHANNEL.value: "Канал (подписка)",
    CampaignKind.BOT.value: "Бот (запуск)",
    CampaignKind.POST.value: "Пост (просмотр)",
    CampaignKind.CUSTOM.value: "Своё задание",
}

CAMPAIGN_KIND_EMOJI: dict[str, str] = {
    CampaignKind.CHANNEL.value: "📢",
    CampaignKind.BOT.value: "🤖",
    CampaignKind.POST.value: "📝",
    CampaignKind.CUSTOM.value: "🔗",
}


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    MODERATION = "moderation"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    REJECTED = "rejected"


CAMPAIGN_STATUS_LABELS: dict[str, str] = {
    CampaignStatus.DRAFT.value: "черновик",
    CampaignStatus.AWAITING_PAYMENT.value: "ждёт оплаты",
    CampaignStatus.MODERATION.value: "на модерации",
    CampaignStatus.ACTIVE.value: "активна",
    CampaignStatus.PAUSED.value: "на паузе",
    CampaignStatus.DONE.value: "завершена",
    CampaignStatus.REJECTED.value: "отклонена",
}

OPEN_CAMPAIGN_STATUSES: tuple[str, ...] = (
    CampaignStatus.ACTIVE.value,
    CampaignStatus.PAUSED.value,
    CampaignStatus.MODERATION.value,
)


class Campaign(Base):
    """A promotion bought by a user with Telegram Stars (CPA: pay per completion)."""

    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    # Only for CHANNEL/BOT: what getChatMember is called with (@username or -100…).
    check_chat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reward: Mapped[int] = mapped_column(Integer)
    target_count: Mapped[int] = mapped_column(Integer)
    done_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    xtr_price: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.DRAFT.value)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    moderation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def budget_left(self) -> int:
        return max(self.target_count - self.done_count, 0)

    @property
    def is_visible(self) -> bool:
        return self.status == CampaignStatus.ACTIVE.value and self.budget_left > 0


class CampaignClaim(Base):
    __tablename__ = "campaign_claims"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id", name="uq_campaign_claim"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    reward: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
