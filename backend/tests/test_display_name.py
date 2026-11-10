"""Car names in emails read the way the app shows them (mirrors frontend/tests/displayName.test.ts)."""

import pytest

from src.vehicles.display_name import display_name, display_vehicle


@pytest.mark.parametrize(
    "raw,shown",
    [
        ("CHEVROLET", "Chevrolet"),
        ("GRAND CHEROKEE", "Grand Cherokee"),
        ("KIA", "Kia"),
        ("SILVERADO EV", "Silverado EV"),
        ("MERCEDES-BENZ", "Mercedes-Benz"),
        ("GMC", "GMC"),
        ("BMW", "BMW"),
        ("RAV4", "RAV4"),
        ("MAZDA3", "Mazda3"),
        ("A5", "A5"),
        ("F 150", "F 150"),
        ("MCLAREN", "McLaren"),
        ("", ""),
        (None, ""),
    ],
)
def test_names_are_shown_as_the_app_shows_them(raw, shown):
    assert display_name(raw) == shown


def test_a_vehicle_is_year_make_model():
    assert display_vehicle(2024, "CHEVROLET", "SILVERADO EV") == "2024 Chevrolet Silverado EV"
    assert display_vehicle("2023", "AUDI", "A5") == "2023 Audi A5"
