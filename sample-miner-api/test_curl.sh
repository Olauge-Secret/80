#!/bin/bash
# Test script for internet_search endpoint

API_KEY="E2B5sUgiFQAsqemZXopJzfHZ7gjZ9krzMm27GfuFx94"
URL="http://localhost:8003/internet_search"

echo "Testing internet_search endpoint..."
echo ""

curl -X POST \
  "$URL" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d @test_internet_search.json

echo ""
echo ""
