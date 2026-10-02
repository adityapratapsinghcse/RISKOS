from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_gis.filters import InBBoxFilter
from django.db.models import Sum, F, Count, Avg, Q
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.gis.db.models.functions import Distance
from .models import Habitation, SafeSite, SimulationLog
from .serializers import (
    HabitationSerializer,
    HabitationDetailSerializer,
    SafeSiteSerializer,
    SafeSiteMatchSerializer,
    SimulationLogSerializer,
)
from .matching import find_safe_sites_for


# ─── Hazard-type filter helpers ───────────────────────────────────────────────

def _hazard_type_filter(qs, hazard_type):
    """Filter habitations by dominant hazard type using hazard_type_flags JSON field."""
    if hazard_type and hazard_type.lower() in ('flood', 'seismic', 'landslide', 'cloudburst'):
        key = hazard_type.lower()
        # JSONField lookup: hazard_type_flags contains key = True
        return qs.filter(**{f'hazard_type_flags__{key}': True})
    return qs


# ─── Damage / travel time helpers (used by simulation) ────────────────────────

def _damage_severity(distance_from_epi_km, radius_km, intensity, pct_kutcha, pct_dilapidated):
    """0–1 score converted to categorical severity."""
    proximity = max(0.0, 1.0 - distance_from_epi_km / radius_km) if radius_km > 0 else 1.0
    vulnerability = (pct_kutcha * 0.5 + pct_dilapidated * 0.5) / 100.0
    score = (proximity * 0.55 + vulnerability * 0.45) * (intensity / 10.0)
    if score >= 0.72:
        return 'CATASTROPHIC'
    if score >= 0.52:
        return 'SEVERE'
    if score >= 0.32:
        return 'HIGH'
    if score >= 0.16:
        return 'MODERATE'
    return 'LOW'


def _structures_at_risk(population, damage_severity):
    """Rough estimate: ~5 persons per household."""
    factor = {
        'CATASTROPHIC': 0.90,
        'SEVERE': 0.70,
        'HIGH': 0.50,
        'MODERATE': 0.25,
        'LOW': 0.08,
    }.get(damage_severity, 0.25)
    return int(population * 0.20 * factor)


def _travel_time_min(distance_km, disaster_type):
    """Estimate evacuation travel time in minutes based on road conditions for the disaster type."""
    avg_speed_kmh = {
        'CLOUDBURST': 25,
        'FLOOD': 20,
        'LANDSLIDE': 15,
        'EARTHQUAKE': 30,
    }.get(disaster_type, 25)
    return round((distance_km / avg_speed_kmh) * 60, 1) if avg_speed_kmh > 0 else 0


def _infra_outage_pct(damage_index, disaster_type):
    """Rough infrastructure outage % by damage level and disaster type."""
    base = {
        'CATASTROPHIC': 95, 'SEVERE': 75, 'HIGH': 55, 'MODERATE': 30, 'LOW': 10,
    }.get(damage_index, 30)
    multiplier = {
        'LANDSLIDE': 1.20,   # road cuts likely
        'FLOOD': 1.10,
        'CLOUDBURST': 1.05,
        'EARTHQUAKE': 1.15,
    }.get(disaster_type, 1.0)
    return min(100, round(base * multiplier))


# ─── ViewSets ─────────────────────────────────────────────────────────────────

class HabitationViewSet(viewsets.ModelViewSet):
    queryset = Habitation.objects.all()
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    bbox_filter_field = "location"
    filter_backends = [InBBoxFilter]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return HabitationDetailSerializer
        return HabitationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        district = self.request.query_params.get("district")
        hazard_level = self.request.query_params.get("hazard_level")
        hazard_type = self.request.query_params.get("hazard_type")
        if district:
            qs = qs.filter(district__iexact=district)
        if hazard_level:
            qs = qs.filter(hazard_level=hazard_level)
        if hazard_type:
            qs = _hazard_type_filter(qs, hazard_type)
        return qs

    @action(detail=True, methods=["get"])
    def safe_sites(self, request, pk=None):
        habitation = self.get_object()
        matches = find_safe_sites_for(habitation)
        serializer = SafeSiteMatchSerializer(matches, many=True)
        return Response(serializer.data)


class SafeSiteViewSet(viewsets.ModelViewSet):
    queryset = SafeSite.objects.all()
    serializer_class = SafeSiteSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        district = self.request.query_params.get("district")
        road_access = self.request.query_params.get("road_access")
        water = self.request.query_params.get("water")
        if district:
            qs = qs.filter(district__iexact=district)
        if road_access is not None:
            qs = qs.filter(road_access=(road_access.lower() == 'true'))
        if water is not None:
            qs = qs.filter(water_availability=(water.lower() == 'true'))
        return qs


# ─── Stats & aggregation views ────────────────────────────────────────────────

class GeoStatsView(APIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get(self, request, *args, **kwargs):
        habs = Habitation.objects.all()
        safes = SafeSite.objects.all()

        total_habs = habs.count()
        red_count = habs.filter(hazard_level="RED").count()
        high_count = habs.filter(hazard_level="HIGH").count()
        mod_count = habs.filter(hazard_level="MODERATE").count()
        safe_count = habs.filter(hazard_level="SAFE").count()

        at_risk = habs.filter(hazard_level__in=["RED", "HIGH"]).aggregate(total=Sum("population"))["total"] or 0
        cap = safes.aggregate(cap=Sum(F("estimated_capacity") - F("current_occupied")))["cap"] or 0
        districts = list(habs.values_list("district", flat=True).distinct().order_by("district"))

        return Response({
            "total_habitations": total_habs,
            "red_count": red_count,
            "high_count": high_count,
            "moderate_count": mod_count,
            "safe_count": safe_count,
            "total_population_at_risk": at_risk,
            "total_safe_sites": safes.count(),
            "total_shelter_capacity": cap,
            "districts": districts,
        })


class DistrictSummaryView(APIView):
    """Per-district aggregated vulnerability and capacity summary."""
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get(self, request, *args, **kwargs):
        hab_rows = (
            Habitation.objects
            .values('district')
            .annotate(
                total=Count('id'),
                red_count=Count('id', filter=Q(hazard_level='RED')),
                high_count=Count('id', filter=Q(hazard_level='HIGH')),
                moderate_count=Count('id', filter=Q(hazard_level='MODERATE')),
                safe_count=Count('id', filter=Q(hazard_level='SAFE')),
                total_population=Sum('population'),
                at_risk_population=Sum('population', filter=Q(hazard_level__in=['RED', 'HIGH'])),
                avg_hazard_score=Avg('hazard_score'),
                avg_vulnerability_score=Avg('vulnerability_score'),
            )
            .order_by('-red_count', '-high_count')
        )

        site_rows = (
            SafeSite.objects
            .values('district')
            .annotate(
                site_count=Count('id'),
                total_capacity=Sum('estimated_capacity'),
                available_capacity=Sum(F('estimated_capacity') - F('current_occupied')),
            )
        )
        site_map = {r['district']: r for r in site_rows}

        results = []
        for row in hab_rows:
            district = row['district']
            sinfo = site_map.get(district, {})
            available = sinfo.get('available_capacity') or 0
            at_risk = row['at_risk_population'] or 0
            coverage = round((available / at_risk * 100) if at_risk > 0 else 100.0, 1)

            results.append({
                **row,
                'avg_hazard_score': round(row['avg_hazard_score'] or 0, 1),
                'avg_vulnerability_score': round(row['avg_vulnerability_score'] or 0, 1),
                'site_count': sinfo.get('site_count', 0),
                'total_shelter_capacity': sinfo.get('total_capacity', 0),
                'available_shelter_capacity': available,
                'capacity_gap': max(0, at_risk - available),
                'coverage_pct': coverage,
            })

        return Response({'count': len(results), 'results': results})


class PriorityReportView(APIView):
    """
    Returns the top-N habitations ranked by composite risk score.
    Implements the PS-26191 requirement:
    'Prioritize vulnerable habitations for immediate, short-term, medium-term relocation.'
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        top_n = int(request.query_params.get('n', 50))
        district = request.query_params.get('district')
        hazard_type = request.query_params.get('hazard_type')

        qs = Habitation.objects.filter(hazard_level__in=['RED', 'HIGH', 'MODERATE'])
        if district:
            qs = qs.filter(district__iexact=district)
        if hazard_type:
            qs = _hazard_type_filter(qs, hazard_type)

        # Sort by composite: 60% hazard, 40% vulnerability
        habs = list(qs.order_by('-hazard_score', '-vulnerability_score')[:top_n])

        results = []
        for i, h in enumerate(habs):
            composite = round((h.hazard_score * 0.6) + (h.vulnerability_score * 0.4), 2)
            if composite >= 55:
                priority = 'IMMEDIATE'
            elif composite >= 40:
                priority = 'SHORT_TERM'
            elif composite >= 25:
                priority = 'MEDIUM_TERM'
            else:
                priority = 'MONITOR'

            flags = h.hazard_type_flags or {}
            active_hazards = [k.upper() for k, v in flags.items() if v]

            results.append({
                'rank': i + 1,
                'id': h.id,
                'name': h.name,
                'district': h.district,
                'state': h.state,
                'population': h.population,
                'hazard_level': h.hazard_level,
                'hazard_score': round(h.hazard_score, 1),
                'vulnerability_score': round(h.vulnerability_score, 1),
                'composite_score': composite,
                'priority': priority,
                'active_hazards': active_hazards,
                'scored_at': h.scored_at,
            })

        return Response({'count': len(results), 'results': results})


# ─── Disaster Simulation ──────────────────────────────────────────────────────

class SimulateDisasterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        data = request.data
        lat = float(data.get('lat', 0))
        lon = float(data.get('lon', 0))
        radius_km = float(data.get('radius_km', 5.0))
        disaster_type = data.get('type', 'CLOUDBURST').upper()
        intensity = float(data.get('intensity', 5.0))  # 1–10 scale

        epicenter = Point(lon, lat, srid=4326)

        # 1. Find all settlements inside the hazard radius
        affected = (
            Habitation.objects
            .filter(location__distance_lte=(epicenter, D(km=radius_km)))
            .annotate(dist_from_epicenter=Distance('location', epicenter))
            .order_by('dist_from_epicenter')
        )

        # 2. Build capacity tracker for safe sites (outside the danger zone)
        all_safe_sites = SafeSite.objects.exclude(
            location__distance_lte=(epicenter, D(km=radius_km))
        )
        safe_site_tracker = {s.id: {'site': s, 'remaining': s.remaining_capacity()} for s in all_safe_sites}

        results = []
        total_pop = 0
        total_structures = 0
        total_displaced = 0
        route_distances = []
        route_times = []
        activated_sites = set()

        for hab in affected:
            dist_km = hab.dist_from_epicenter.km if hasattr(hab, 'dist_from_epicenter') else 0.0

            # Damage metrics for this settlement
            damage_sev = _damage_severity(
                dist_km, radius_km, intensity,
                hab.pct_kutcha_roof_wall, hab.pct_dilapidated_housing
            )
            structures = _structures_at_risk(hab.population, damage_sev)
            displaced = int(hab.population * {
                'CATASTROPHIC': 1.0, 'SEVERE': 0.85, 'HIGH': 0.65, 'MODERATE': 0.40, 'LOW': 0.15,
            }.get(damage_sev, 0.4))

            # 3. Assign nearest safe site with enough capacity
            assigned_site = None
            route_distance = None
            travel_time = None

            closest_outside = sorted(
                [v for v in safe_site_tracker.values() if v['remaining'] > 0],
                key=lambda v: v['site'].location.distance(epicenter)
            )

            for item in closest_outside:
                site = item['site']
                if item['remaining'] >= displaced:
                    dist_to_site = round(
                        site.location.distance(Point(hab.location.x, hab.location.y, srid=4326)) * 111.32,
                        2
                    )
                    travel_time_est = _travel_time_min(dist_to_site, disaster_type)
                    assigned_site = {
                        'id': site.id,
                        'name': site.name,
                        'lat': site.location.y,
                        'lon': site.location.x,
                        'district': site.district,
                        'distance_km': dist_to_site,
                        'estimated_travel_min': travel_time_est,
                    }
                    safe_site_tracker[site.id]['remaining'] -= displaced
                    activated_sites.add(site.id)
                    route_distance = dist_to_site
                    travel_time = travel_time_est
                    break

            if route_distance is not None:
                route_distances.append(route_distance)
            if travel_time is not None:
                route_times.append(travel_time)

            results.append({
                'id': hab.id,
                'name': hab.name,
                'population': hab.population,
                'lat': hab.location.y,
                'lon': hab.location.x,
                'district': hab.district,
                'distance_from_epicenter_km': round(dist_km, 2),
                'damage_severity': damage_sev,
                'estimated_structures_at_risk': structures,
                'estimated_displaced': displaced,
                'assigned_safe_site': assigned_site,
            })
            total_pop += hab.population
            total_structures += structures
            total_displaced += displaced

        # 4. Build impact summary
        all_damage_levels = [r['damage_severity'] for r in results]
        if all_damage_levels.count('CATASTROPHIC') + all_damage_levels.count('SEVERE') >= len(all_damage_levels) * 0.4:
            overall_damage = 'CATASTROPHIC'
        elif all_damage_levels.count('HIGH') >= len(all_damage_levels) * 0.3:
            overall_damage = 'SEVERE'
        elif all_damage_levels.count('MODERATE') >= len(all_damage_levels) * 0.3:
            overall_damage = 'HIGH'
        else:
            overall_damage = 'MODERATE'

        total_shelter_available = sum(
            v['site'].remaining_capacity() for v in safe_site_tracker.values()
            if v['site'].id in activated_sites
        ) + total_displaced  # before allocation
        capacity_coverage = round(
            (min(total_displaced, sum(
                v['site'].remaining_capacity() for v in safe_site_tracker.values()
            ) + total_displaced) / total_displaced * 100)
            if total_displaced > 0 else 100.0,
            1
        )

        avg_distance = round(sum(route_distances) / len(route_distances), 2) if route_distances else 0
        avg_time = round(sum(route_times) / len(route_times), 1) if route_times else 0
        infra_outage = _infra_outage_pct(overall_damage, disaster_type)
        unaccommodated = sum(
            r['estimated_displaced'] for r in results if r['assigned_safe_site'] is None
        )

        impact_summary = {
            'affected_settlements': len(results),
            'total_affected_population': total_pop,
            'total_displaced_estimate': total_displaced,
            'estimated_structures_at_risk': total_structures,
            'damage_index': overall_damage,
            'infrastructure_outage_pct': infra_outage,
            'safe_sites_activated': len(activated_sites),
            'capacity_coverage_pct': min(100.0, capacity_coverage),
            'unaccommodated_population': unaccommodated,
            'avg_route_distance_km': avg_distance,
            'avg_travel_time_min': avg_time,
        }

        # 5. Log simulation if authenticated user
        if request.user and request.user.is_authenticated:
            SimulationLog.objects.create(
                ran_by=request.user,
                disaster_type=disaster_type,
                epicenter_lat=lat,
                epicenter_lon=lon,
                radius_km=radius_km,
                intensity=intensity,
                affected_count=len(results),
                total_displaced=total_displaced,
                estimated_structures_at_risk=total_structures,
                damage_index=overall_damage,
                safe_sites_activated=len(activated_sites),
                avg_route_distance_km=avg_distance,
                avg_travel_time_min=avg_time,
            )

        return Response({
            'epicenter': {'lat': lat, 'lon': lon, 'radius_km': radius_km, 'type': disaster_type, 'intensity': intensity},
            'affected_habitations': results,
            'impact_summary': impact_summary,
            'total_affected_population': total_pop,  # kept for backward compat
        })


# ─── System / Admin views ─────────────────────────────────────────────────────

class SystemHealthView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        import time
        import os

        t0 = time.time()
        hab_count = Habitation.objects.count()
        db_latency = round((time.time() - t0) * 1000, 2)

        site_count = SafeSite.objects.count()

        from relocation.models import RelocationPlan, Alert
        plan_count = RelocationPlan.objects.count()
        alert_count = Alert.objects.count()
        sim_count = SimulationLog.objects.count()

        last_scored = (
            Habitation.objects
            .filter(scored_at__isnull=False)
            .order_by('-scored_at')
            .values_list('scored_at', flat=True)
            .first()
        )

        model_dir = 'backend/ai_engine/models'
        model_files = {
            'xgboost': os.path.exists(f'{model_dir}/xgboost_hazard_model.pkl'),
            'isolation_forest': os.path.exists(f'{model_dir}/iso_forest_vuln_model.pkl'),
            'scaler': os.path.exists(f'{model_dir}/vuln_scaler.pkl'),
        }
        ai_status = 'trained' if all(model_files.values()) else ('partial' if any(model_files.values()) else 'missing')

        return Response({
            'status': 'healthy',
            'database': {
                'habitations': hab_count,
                'safe_sites': site_count,
                'relocation_plans': plan_count,
                'alerts': alert_count,
                'simulations_logged': sim_count,
                'latency_ms': db_latency,
            },
            'ai_models': {
                'status': ai_status,
                'files_present': model_files,
                'last_scored_at': last_scored,
            },
            'postgis': 'active',
        })


class TriggerCommandView(APIView):
    """SUPERADMIN-only: trigger backend management commands via REST API."""
    permission_classes = [permissions.IsAuthenticated]

    ALLOWED_COMMANDS = {
        'compute_hazard_scores': 'Recompute hazard & vulnerability scores for all habitations',
        'fetch_live_alerts': 'Fetch live weather forecast and auto-create alerts',
        'run_ai_scoring': 'Train and run ML models (XGBoost + Isolation Forest)',
    }

    def post(self, request, command, *args, **kwargs):
        if not hasattr(request.user, 'role') or request.user.role != 'SUPERADMIN':
            return Response({'error': 'Superadmin access required.'}, status=403)
        if command not in self.ALLOWED_COMMANDS:
            return Response(
                {'error': f'Unknown command. Allowed: {list(self.ALLOWED_COMMANDS.keys())}'},
                status=400
            )
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        try:
            call_command(command, stdout=out)
            return Response({'status': 'success', 'command': command, 'output': out.getvalue()})
        except Exception as exc:
            return Response({'status': 'error', 'command': command, 'message': str(exc)}, status=500)
