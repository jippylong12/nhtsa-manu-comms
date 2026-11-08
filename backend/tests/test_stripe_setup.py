"""scripts/stripe_setup: the two test-mode prices, created idempotently by lookup key (JIP-1591).

A fake stripe module is the only boundary; no real Stripe call is made.
"""

from types import SimpleNamespace

from src.scripts import stripe_setup


class FakeStripe:
    def __init__(self, existing: dict[str, str] | None = None):
        self.existing = existing or {}
        self.list_calls: list[dict] = []
        self.create_calls: list[dict] = []
        outer = self

        class _Price:
            @staticmethod
            def list(**kwargs):
                outer.list_calls.append(kwargs)
                return {
                    "data": [
                        {"id": outer.existing[k], "lookup_key": k}
                        for k in kwargs["lookup_keys"]
                        if k in outer.existing
                    ]
                }

            @staticmethod
            def create(**kwargs):
                outer.create_calls.append(kwargs)
                return {"id": "price_new_" + kwargs["lookup_key"]}

        self.Price = _Price
        self.Product = SimpleNamespace()


def test_dry_run_is_the_default_and_calls_nothing(capsys):
    fake = FakeStripe()
    assert stripe_setup.main([], stripe_module=fake) == 0
    out = capsys.readouterr().out
    assert "nhtsa_unlock_one_time" in out and "nhtsa_monthly" in out
    assert "300" in out and "500" in out
    assert fake.list_calls == [] and fake.create_calls == []


def test_apply_creates_both_prices_and_prints_env_lines(capsys):
    fake = FakeStripe()
    assert stripe_setup.main(["--apply"], stripe_module=fake) == 0
    out = capsys.readouterr().out
    assert "STRIPE_PRICE_UNLOCK=price_new_nhtsa_unlock_one_time" in out
    assert "STRIPE_PRICE_MONTHLY=price_new_nhtsa_monthly" in out
    unlock, monthly = fake.create_calls
    assert (unlock["unit_amount"], unlock["currency"]) == (300, "usd")
    assert "recurring" not in unlock
    assert unlock["product_data"] == {"name": "Vehicle unlock"}
    assert (monthly["unit_amount"], monthly["currency"]) == (500, "usd")
    assert monthly["recurring"] == {"interval": "month"}
    assert monthly["product_data"] == {"name": "NHTSA Comms monthly"}


def test_apply_reuses_existing_lookup_keys(capsys):
    fake = FakeStripe({"nhtsa_unlock_one_time": "price_old_unlock", "nhtsa_monthly": "price_old_m"})
    assert stripe_setup.main(["--apply"], stripe_module=fake) == 0
    out = capsys.readouterr().out
    assert "STRIPE_PRICE_UNLOCK=price_old_unlock" in out
    assert "STRIPE_PRICE_MONTHLY=price_old_m" in out
    assert fake.create_calls == []
    assert fake.list_calls[0]["lookup_keys"] == ["nhtsa_unlock_one_time"]


def test_apply_creates_only_the_missing_price(capsys):
    fake = FakeStripe({"nhtsa_monthly": "price_old_m"})
    stripe_setup.main(["--apply"], stripe_module=fake)
    out = capsys.readouterr().out
    assert [c["lookup_key"] for c in fake.create_calls] == ["nhtsa_unlock_one_time"]
    assert "STRIPE_PRICE_MONTHLY=price_old_m" in out
