from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import (
    HabitationViewSet,
    SafeSiteViewSet,
    GeoStatsView,
    DistrictSummaryView,
    PriorityReportView,
    SimulateDisasterView,
    SystemHealthView,
    TriggerCommandView,
)

router = DefaultRouter()
router.register("habitations", HabitationViewSet)
router.register("safesites", SafeSiteViewSet)

urlpatterns = [
    path('stats/', GeoStatsView.as_view(), name='geostats'),
    path('district-summary/', DistrictSummaryView.as_view(), name='district_summary'),
    path('priority-report/', PriorityReportView.as_view(), name='priority_report'),
    path('simulate-disaster/', SimulateDisasterView.as_view(), name='simulate_disaster'),
    path('system/health/', SystemHealthView.as_view(), name='system_health'),
    path('system/trigger/<str:command>/', TriggerCommandView.as_view(), name='trigger_command'),
] + router.urls