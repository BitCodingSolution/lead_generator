# Run the backend with the public tracking URL set.
# Use this instead of plain `uvicorn` once api.bitcodingsolutions.com is live,
# so every outgoing email's tracking pixel points at the public endpoint.

$env:LINKEDIN_TRACKING_BASE_URL = "https://api.bitcodingsolutions.com"
python -m uvicorn main:app --host 127.0.0.1 --port 8900
