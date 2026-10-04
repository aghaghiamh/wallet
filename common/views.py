from django.conf import settings
from django.db import connections
from django.urls import path
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularJSONAPIView, SpectacularSwaggerView
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()


class HealthView(APIView):
    @extend_schema(responses=HealthSerializer)
    def get(self, request):
        alias = "provider" if settings.SERVICE_ROLE == "provider" else "default"
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})


def common_urls():
    return [
        path("health", HealthView.as_view()),
        path("openapi.json", SpectacularJSONAPIView.as_view(), name="schema"),
        path("docs", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ]
