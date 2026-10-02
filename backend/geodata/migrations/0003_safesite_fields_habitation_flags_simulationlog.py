import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geodata', '0002_habitation_avg_annual_rainfall_mm_and_more'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        # --- Habitation: add hazard_type_flags ---
        migrations.AddField(
            model_name='habitation',
            name='hazard_type_flags',
            field=models.JSONField(blank=True, default=dict),
        ),

        # --- SafeSite: add medical_facility_nearby ---
        migrations.AddField(
            model_name='safesite',
            name='medical_facility_nearby',
            field=models.BooleanField(default=False),
        ),

        # --- SafeSite: add shelter_type ---
        migrations.AddField(
            model_name='safesite',
            name='shelter_type',
            field=models.CharField(default='GOVT_BUILDING', max_length=30),
        ),

        # --- Create SimulationLog ---
        migrations.CreateModel(
            name='SimulationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('disaster_type', models.CharField(
                    choices=[
                        ('CLOUDBURST', 'Cloudburst / Extreme Rainfall'),
                        ('FLOOD', 'Riverine Flood'),
                        ('LANDSLIDE', 'Landslide / Slope Failure'),
                        ('EARTHQUAKE', 'Earthquake / Seismic Shock'),
                    ],
                    default='CLOUDBURST',
                    max_length=20,
                )),
                ('epicenter_lat', models.FloatField()),
                ('epicenter_lon', models.FloatField()),
                ('radius_km', models.FloatField()),
                ('intensity', models.FloatField(default=5.0)),
                ('affected_count', models.IntegerField(default=0)),
                ('total_displaced', models.IntegerField(default=0)),
                ('estimated_structures_at_risk', models.IntegerField(default=0)),
                ('damage_index', models.CharField(default='MODERATE', max_length=20)),
                ('safe_sites_activated', models.IntegerField(default=0)),
                ('avg_route_distance_km', models.FloatField(default=0)),
                ('avg_travel_time_min', models.FloatField(default=0)),
                ('ran_at', models.DateTimeField(auto_now_add=True)),
                ('converted_to_plans', models.BooleanField(default=False)),
                ('ran_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='simulations',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
