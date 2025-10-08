"""Configuration constants for nhtsa-manu-comms."""

# API configuration
VEHICLE_ID = 218944
DETAILS_URL = f"https://api.nhtsa.gov/vehicles/{VEHICLE_ID}/details"
DETAILS_PARAMS = {
    "data": "complaints,recalls,investigations,manufacturercommunications",
    "productDetail": "minimal",
    "name": "",
}
SAFETY_ISSUES_URL = "https://api.nhtsa.gov/safetyIssues/byNhtsaId"

# Concurrency and caching
MAX_WORKERS = 5
CACHE_DIR = ".cache"

# Product filters
TARGET_YEAR = "2024"
TARGET_MODEL = "SILVERADO EV"  # case-insensitive comparison

# Keyword filters for the summary field
KEYWORDS = (
    "sidewinder",
    "software update",
    "update",
    "reprogram",
    "reprogramming",
    "calibration",
    "flash",
)
