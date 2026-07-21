"""Matching a tracked vehicle's model against a bulletin's associatedProducts.

/vehicles/models names a vehicle "F-150 SUPER CREW" while the bulletins that
apply to it name the product "F-150", so the two have to be reconciled. The
Mongo-backed tests run against a real local MongoDB in a throwaway database so
the $elemMatch and the escaped $regex are the real ones.
"""

import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from src.communications.service import CommunicationService
from src.config import get_settings

match = CommunicationService._model_matches


# --------------------------------------------------------------------------- #
# The name reconciliation itself
# --------------------------------------------------------------------------- #
def test_bulletin_base_model_matches_cab_style_qualified_vehicle():
    """The reported bug: 2020 F-150 SUPER CREW counted 0 of its 208 bulletins."""
    assert match("F-150 SUPER CREW", "F-150")
    assert match("F-150 SUPERCAB", "F-150")
    assert match("F-250 CREW CAB", "F-250")


def test_match_is_symmetric():
    assert match("F-150", "F-150 SUPER CREW")


def test_exact_names_still_match():
    assert match("CIVIC", "CIVIC")
    assert match("civic", "CIVIC")


def test_whole_token_comparison_rejects_partial_words():
    """The guard against a substring match: F-150 must not match F-1500."""
    assert not match("F-1500", "F-150")
    assert not match("F-150", "F-1500")
    assert not match("CIVIC", "CIVIC-R")


def test_unrelated_models_do_not_match():
    assert not match("F-150 SUPER CREW", "EXPEDITION")
    assert not match("CIVIC", "ACCORD")
    assert not match("RANGER", "F-150")


def test_divergence_after_a_shared_first_token_does_not_match():
    assert not match("F-150 SUPER CREW", "F-150 SUPERCAB DIESEL")
    assert not match("TRANSIT LOW ROOF 15 PASS", "TRANSIT HIGH ROOF 15 PASS")


def test_empty_names_never_match():
    assert not match("", "F-150")
    assert not match("F-150", "")
    assert not match("   ", "F-150")


# --------------------------------------------------------------------------- #
# The Mongo query has to agree with the function above
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def comms():
    """A throwaway communications collection."""
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url, serverSelectionTimeoutMS=3000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("MongoDB not reachable at " + settings.mongodb_url)

    db_name = f"nhtsa_comms_test_{uuid.uuid4().hex[:12]}"
    db = client[db_name]
    yield db.communications
    await client.drop_database(db_name)
    client.close()


async def _count(comms, vehicle_id, year, model):
    query = CommunicationService._product_match_query(vehicle_id, year, model)
    return await comms.count_documents(query)


@pytest.mark.asyncio
async def test_query_counts_a_bulletin_naming_only_the_base_model(comms):
    """Shaped like a real F-150 bulletin: many products, none cab-qualified."""
    await comms.insert_one(
        {
            "vehicle_id": 11181,
            "nhtsa_id": 11018521,
            "associated_products": [
                {"product_year": "2020", "product_model": "F-150", "product_make": "FORD"},
                {"product_year": "2020", "product_model": "EXPEDITION", "product_make": "FORD"},
                {"product_year": "2020", "product_model": "F-250 SD", "product_make": "FORD"},
            ],
        }
    )

    assert await _count(comms, 11181, "2020", "F-150 SUPER CREW") == 1


@pytest.mark.asyncio
async def test_query_rejects_a_near_miss_model(comms):
    await comms.insert_one(
        {
            "vehicle_id": 999,
            "nhtsa_id": 1,
            "associated_products": [
                {"product_year": "2020", "product_model": "F-1500", "product_make": "FORD"}
            ],
        }
    )

    assert await _count(comms, 999, "2020", "F-150") == 0


@pytest.mark.asyncio
async def test_query_requires_year_and_model_on_the_same_product(comms):
    """A 2020 Expedition plus a 2019 F-150 is not a 2020 F-150."""
    await comms.insert_one(
        {
            "vehicle_id": 998,
            "nhtsa_id": 2,
            "associated_products": [
                {"product_year": "2020", "product_model": "EXPEDITION", "product_make": "FORD"},
                {"product_year": "2019", "product_model": "F-150", "product_make": "FORD"},
            ],
        }
    )

    assert await _count(comms, 998, "2020", "F-150 SUPER CREW") == 0


@pytest.mark.asyncio
async def test_model_names_containing_regex_metacharacters_are_literal(comms):
    """ "CLA 350 4MATIC (EV)" is a real dropdown entry. Unescaped, its parens
    become a group that matches "CLA 350 4MATIC EV" and misses the real value."""
    await comms.insert_many(
        [
            {
                "vehicle_id": 997,
                "nhtsa_id": 3,
                "associated_products": [
                    {"product_year": "2024", "product_model": "CLA 350 4MATIC (EV)"}
                ],
            },
            {
                "vehicle_id": 997,
                "nhtsa_id": 4,
                "associated_products": [
                    {"product_year": "2024", "product_model": "CLA 350 4MATIC EV"}
                ],
            },
        ]
    )

    assert await _count(comms, 997, "2024", "CLA 350 4MATIC (EV)") == 1


@pytest.mark.asyncio
async def test_a_model_name_with_a_bracket_does_not_break_the_query(comms):
    """An unescaped "[" makes Mongo reject the regex outright, failing the fetch."""
    await comms.insert_one(
        {
            "vehicle_id": 996,
            "nhtsa_id": 5,
            "associated_products": [{"product_year": "2020", "product_model": "RANGER [XLT"}],
        }
    )

    assert await _count(comms, 996, "2020", "RANGER [XLT") == 1


@pytest.mark.asyncio
async def test_missing_year_or_model_counts_everything_for_the_vehicle(comms):
    await comms.insert_one({"vehicle_id": 995, "nhtsa_id": 6, "associated_products": []})

    assert await _count(comms, 995, "", "") == 1
