"""Create the two Stripe prices the app sells, idempotently by lookup key.

    python -m src.scripts.stripe_setup --dry-run   # default: print what would be created
    python -m src.scripts.stripe_setup --apply     # create (or reuse) and print the .env lines

Reuses a price that already carries the lookup key, so re-running is safe. Use a TEST-mode
`STRIPE_SECRET_KEY`; the printed `STRIPE_PRICE_*` ids are not secrets.
"""

import argparse
import sys
from typing import Any

PRICES = [
    {
        "env": "STRIPE_PRICE_UNLOCK",
        "lookup_key": "nhtsa_unlock_one_time",
        "product": "Vehicle unlock",
        "amount": 300,
        "recurring": None,
    },
    {
        "env": "STRIPE_PRICE_MONTHLY",
        "lookup_key": "nhtsa_monthly",
        "product": "NHTSA Comms monthly",
        "amount": 500,
        "recurring": {"interval": "month"},
    },
]


def _describe(spec: dict) -> str:
    cadence = "monthly" if spec["recurring"] else "one-time"
    return (
        f"{spec['lookup_key']}: product \"{spec['product']}\", {cadence} price "
        f"{spec['amount']} cents USD"
    )


def apply(stripe: Any) -> dict[str, str]:
    """Create or reuse each price; the `.env` name mapped to its price id."""
    ids: dict[str, str] = {}
    for spec in PRICES:
        found = stripe.Price.list(lookup_keys=[spec["lookup_key"]], limit=1)["data"]
        if found:
            ids[spec["env"]] = found[0]["id"]
            continue
        params: dict = {
            "currency": "usd",
            "unit_amount": spec["amount"],
            "lookup_key": spec["lookup_key"],
            "product_data": {"name": spec["product"]},
        }
        if spec["recurring"]:
            params["recurring"] = spec["recurring"]
        ids[spec["env"]] = stripe.Price.create(**params)["id"]
    return ids


def main(argv: list[str] | None = None, stripe_module: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Create the NHTSA Stripe test prices.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan (the default)")
    mode.add_argument("--apply", action="store_true", help="create or reuse the prices")
    args = parser.parse_args(argv)

    if not args.apply:
        print("Dry run: would create (or reuse by lookup key):")
        for spec in PRICES:
            print("  " + _describe(spec))
        print("Run with --apply to create them.")
        return 0

    if stripe_module is None:
        from src.billing import stripe_client

        try:
            stripe_module = stripe_client.client()
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
    for name, price_id in apply(stripe_module).items():
        print(f"{name}={price_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
