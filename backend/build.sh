#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate

# Only seed data when the habitations table is empty (fresh DB) to avoid duplicates on redeploy
HABS_COUNT=$(python manage.py shell -c "from geodata.models import Habitation; print(Habitation.objects.count())" 2>/dev/null || echo "0")
if [ "$HABS_COUNT" -eq "0" ]; then
  echo "Empty DB detected – loading seed data..."
  python manage.py loaddata db_dump.json
else
  echo "DB already has $HABS_COUNT habitations – skipping loaddata."
fi
