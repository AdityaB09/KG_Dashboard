# EXACT LOCATION 1:
# backend/main.py
#
# Add this import beside the other app router imports.

from app.phase7.routes import (
    router as phase7_router,
)


# EXACT LOCATION 2:
# Add this after the existing episode and incident
# include_router calls.

app.include_router(phase7_router)
